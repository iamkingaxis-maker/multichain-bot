# core/watchlist_pruner.py
"""Auto-pruner for the user-curated watchlist (2026-06-03).

The user manually curates a watchlist of tokens to surface. Over time tokens on
it DIE (rug to near-zero, dry up to no volume, lose all liquidity) -- the base-rate
deep dive found ~70% of fresh grads round-trip to dust. A dead token on the
watchlist is wasted enrichment/egress and can still get bought on a fake-life
blip. This module decides which watchlist tokens are DEAD so the scanner can
auto-remove them (in-process, via remove_user_watchlist -- no dashboard auth path).

Liveness criterion (same family as the shipped range90 / dead-flatline gates):
a token is DEAD if it has rugged (mcap<=0 or liq<=0), is untradeable (liq below
floor), or has dried up (24h volume below floor). FAIL-OPEN: a token we could NOT
assess (no fresh data this cycle) is NEVER marked dead -- we only prune on positive
evidence of death. The scanner additionally requires N consecutive dead readings
(strikes) before removing, so a momentarily-quiet token is not pruned on one blip.

All functions are pure (env read at the edges) so they are unit-testable.
"""
from __future__ import annotations
import os
from typing import Optional


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _flag("WATCHLIST_AUTOPRUNE", "1")  # default ON


def min_vol_h24() -> float:
    try:
        return float(os.environ.get("WATCHLIST_PRUNE_MIN_VOL_H24", "25000"))
    except (TypeError, ValueError):
        return 25000.0


def min_liq() -> float:
    try:
        return float(os.environ.get("WATCHLIST_PRUNE_MIN_LIQ", "20000"))
    except (TypeError, ValueError):
        return 20000.0


def strikes_required() -> int:
    # Strikes are CONSECUTIVE SCAN CYCLES (~1min each) now, NOT 30-min intervals -> 2
    # strikes = ~2-3min, a blip-guard that's still near-instant. Rugged tokens bypass
    # strikes entirely (is_rugged) and are removed on first detection.
    try:
        return max(1, int(os.environ.get("WATCHLIST_PRUNE_STRIKES", "2")))
    except (TypeError, ValueError):
        return 2


def is_rugged(token) -> bool:
    """Unambiguously dead: liquidity or market cap reads <= 0 (drained/delisted). A
    live token never has zero liq with a present pair, so these are removed INSTANTLY
    (no strike guard). token = dict with liq_usd / mcap."""
    liq = _num(token.get("liq_usd"))
    mc = _num(token.get("mcap"))
    return (mc is not None and mc <= 0) or (liq is not None and liq <= 0)


# ---- Auto-ADD config (2026-06-03): self-populate the watchlist with fresh live movers ----
def autoadd_enabled() -> bool:
    return _flag("WATCHLIST_AUTOADD", "1")  # default ON


def max_size() -> int:
    try:
        return max(1, int(os.environ.get("WATCHLIST_MAX_SIZE", "150")))
    except (TypeError, ValueError):
        return 150


def add_min_liq() -> float:
    try:
        return float(os.environ.get("WATCHLIST_ADD_MIN_LIQ", "40000"))
    except (TypeError, ValueError):
        return 40000.0


def add_min_vol_h24() -> float:
    try:
        return float(os.environ.get("WATCHLIST_ADD_MIN_VOL_H24", "75000"))
    except (TypeError, ValueError):
        return 75000.0


def add_min_pc_h1() -> float:
    try:
        return float(os.environ.get("WATCHLIST_ADD_MIN_PC_H1", "8"))
    except (TypeError, ValueError):
        return 8.0


def add_max_age_h() -> float:
    try:
        return float(os.environ.get("WATCHLIST_ADD_MAX_AGE_H", "24"))
    except (TypeError, ValueError):
        return 24.0


def add_interval_secs() -> float:
    try:
        return float(os.environ.get("WATCHLIST_AUTOADD_INTERVAL_SECS", "600"))  # 10 min
    except (TypeError, ValueError):
        return 600.0


def add_max_per_run() -> int:
    try:
        return max(1, int(os.environ.get("WATCHLIST_ADD_MAX_PER_RUN", "10")))
    except (TypeError, ValueError):
        return 10


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def find_adds(tokens, current_addrs, denylist, max_total, min_liquidity,
              min_vol, min_pc_h1, max_age_hours, max_per_run) -> list:
    """Return addresses to ADD to the watchlist: FRESH (age<=max_age_hours), tradeable
    (liq>=floor, vol_h24>=floor), and RISING (pc_h1>=floor) movers not already on it
    AND NOT in the denylist (tokens the user manually removed — never auto-re-add).
    Ranked by 24h volume (strongest first), capped by remaining room (max_total) and
    max_per_run. tokens = list of dicts: address, liq_usd, vol_h24, pc_h1, age_h.
    Conservative by design — the watchlist only SURFACES tokens (mcap-bypass for
    discovery); the real triggers/filters still gate any actual buy."""
    cur = set(current_addrs)
    banned = set(denylist or ())
    room = max_total - len(cur)
    if room <= 0:
        return []
    cands = []
    for t in tokens:
        addr = t.get("address")
        if not addr or addr in cur or addr in banned:
            continue
        liq = _num(t.get("liq_usd"))
        vol = _num(t.get("vol_h24"))
        pch1 = _num(t.get("pc_h1"))
        age = _num(t.get("age_h"))
        if liq is None or vol is None or pch1 is None:
            continue  # need full evidence to ADD (stricter than prune)
        if liq < min_liquidity or vol < min_vol or pch1 < min_pc_h1:
            continue
        if age is not None and age > max_age_hours:
            continue  # fresh only
        cands.append((vol, addr))
    cands.sort(reverse=True)  # strongest movers (by volume) first
    return [a for _, a in cands[:min(room, max_per_run)]]


def find_dead(tokens, min_vol: Optional[float] = None, min_liquidity: Optional[float] = None) -> list:
    """Return the addresses of DEAD tokens. tokens = list of dicts with keys
    address, liq_usd, vol_h24, mcap. FAIL-OPEN: a token whose liq AND vol are both
    unavailable is NOT marked dead (no evidence). Dead if rugged (mcap<=0 or liq<=0),
    untradeable (liq<floor), or dried up (vol_h24<floor)."""
    if min_vol is None:
        min_vol = min_vol_h24()
    if min_liquidity is None:
        min_liquidity = min_liq()
    dead = []
    for t in tokens:
        addr = t.get("address")
        if not addr:
            continue
        liq = _num(t.get("liq_usd"))
        vol = _num(t.get("vol_h24"))
        mc = _num(t.get("mcap"))
        if liq is None and vol is None:
            continue  # no evidence -> never prune
        # rugged / delisted
        if (mc is not None and mc <= 0) or (liq is not None and liq <= 0):
            dead.append(addr); continue
        # untradeable liquidity
        if liq is not None and liq < min_liquidity:
            dead.append(addr); continue
        # dried-up volume
        if vol is not None and vol < min_vol:
            dead.append(addr); continue
    return dead
