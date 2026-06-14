"""fleet_meta_bus (#436, 2026-06-14) — THE BIG BET: a fleet-wide, equal-weighted,
time-decayed realized $/trade-per-FAMILY signal so the chameleon can rotate off a dying
meta onto the live winner BEFORE its own daily-loss limit is hit.

WHY: the chameleon's only honest pivot signal today is its OWN ~20-close window — it must
personally lose money first to learn a meta died (the 06-14 failure: it stayed on momentum
into the turn and bled out). The fleet runs ~70 bots whose realized P&L per family is a
5-50x larger, LAG-FREE read of what's paying RIGHT NOW (selection is already +EV equal-
weight). This turns that wasted signal into a leading pivot input.

DISCIPLINE (encoded): equal-weight by $/TRADE not sum-$ (avoids the size-is-the-bleed paper
artifact) with a per-bot net CAP + a >=2-distinct-bot consensus rail (one big-size or one
clustered bot can't define the meta — mirrors the sensor's MIN_WALLETS/top_share). SHADOW
first: the chameleon only LOGS what it WOULD rotate to vs what it wears; forward-judge >=14d
beating the current chameleon before it drives real geometry. State proposes, fleet money
disposes. See [[reference_chameleon_green_momentum_2026_06_14]] for the chameleon side.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque

HALFLIFE_SECS = float(os.environ.get("FLEET_BUS_HALFLIFE_SECS", "2700"))       # 45 min
WINDOW_SECS = float(os.environ.get("FLEET_BUS_WINDOW_SECS", str(3 * 3600)))    # keep ~3h
MIN_N = float(os.environ.get("FLEET_BUS_MIN_N", "8"))                          # min decayed trades to rank
MIN_DISTINCT_BOTS = int(os.environ.get("FLEET_BUS_MIN_DISTINCT_BOTS", "2"))    # consensus rail
PER_BOT_NET_CAP = float(os.environ.get("FLEET_BUS_PER_BOT_CAP", "50"))         # clamp one bot's per-trade net

_ring: deque = deque(maxlen=5000)   # (ts, family, net_capped, bot_id)


def record(bot_id, net, ts=None):
    """Feed one realized SELL leg into the bus. Maps bot->family; clamps net to the per-bot
    cap (so a big-size bot can't dominate the equal-weighted read). Fail-soft."""
    try:
        from core.meta_allocator import family_of
        fam = family_of(bot_id)
        if not fam:
            return
        n = max(-PER_BOT_NET_CAP, min(PER_BOT_NET_CAP, float(net or 0)))
        _ring.append((ts or time.time(), fam, n, bot_id))
    except Exception:
        pass


def _agg(now):
    """family -> (decayed_$, decayed_n, distinct_bots) over the live window."""
    cutoff = now - WINDOW_SECS
    d_dollars = defaultdict(float)
    d_n = defaultdict(float)
    bots = defaultdict(set)
    for ts, fam, net, bot in _ring:
        if ts < cutoff:
            continue
        w = 0.5 ** ((now - ts) / HALFLIFE_SECS)
        d_dollars[fam] += net * w
        d_n[fam] += w
        bots[fam].add(bot)
    return d_dollars, d_n, bots


def best_live_family(now=None):
    """(family, decayed_$/trade, decayed_n, distinct_bots) for the family paying best RIGHT
    NOW, or None. Equal-weighted $/trade; requires >=MIN_N decayed trades AND >=2 distinct
    bots (consensus rail). Returns the top family even if its edge is negative (the caller
    decides whether to rotate); pair with a positivity check when enforcing."""
    now = now or time.time()
    d_dollars, d_n, bots = _agg(now)
    best = None
    for fam, dn in d_n.items():
        if dn < MIN_N or len(bots[fam]) < MIN_DISTINCT_BOTS:
            continue
        per = d_dollars[fam] / dn
        if best is None or per > best[1]:
            best = (fam, round(per, 3), round(dn, 1), len(bots[fam]))
    return best


def family_scoreboard(now=None):
    """All families' decayed $/trade + n + distinct-bots (for logging / the dashboard)."""
    now = now or time.time()
    d_dollars, d_n, bots = _agg(now)
    return {fam: {"per_trade": round(d_dollars[fam] / d_n[fam], 3),
                  "n": round(d_n[fam], 1), "bots": len(bots[fam])}
            for fam in d_n if d_n[fam] > 0}
