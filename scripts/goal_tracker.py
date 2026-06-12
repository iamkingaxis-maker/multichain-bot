"""GOAL TRACKER — $100/day paper minimum before live production (set 2026-06-09).

Measures the LIVE-CANDIDATE SET's combined daily P&L (closed trades) vs the $100/day
bar. The candidate set = bots that would plausibly go live, NOT the whole fleet
(the fleet is a selection instrument; its aggregate is meaningless).

Candidate set (update as the scorecard race evolves):
  pond clones (the sharpened entry-stack combos), pool_c_post_peak (scorecard #1),
  pool_c_tightexit, pool_a_candidate, momentum_shadow, + smart_follow strategy trades.

Usage:
  python scripts/goal_tracker.py                  # pull recent from API (last ~5k)
  python scripts/goal_tracker.py --cache FILE     # offline on a cached dump
"""
from __future__ import annotations
import argparse, json, sys, urllib.request, gzip, io
from collections import defaultdict
from datetime import datetime, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

GOAL_USD_PER_DAY = 100.0
CANDIDATE_BOTS = {
    "pond_settled_flow_thin", "pond_settled_flow", "pond_ugly_mtf",
    "pond_settled_flow_solcap", "pond_bb_mtf", "pond_flow_thin",
    # wave 2 (2026-06-10): frontier re-pull — three NEW feature axes
    # (rsi15 oversold, sweep-reclaim, deep-60m-leg), all held-out 82-84% WR
    "pond_ugly_rsi", "pond_sweep_flow", "pond_sweep_deep_thin",
    "pool_c_post_peak", "pool_c_tightexit", "pool_a_candidate", "momentum_shadow",
    # young-pond probers (2026-06-09): top of the live-candidate scorecard on REALIZED
    # results (+$2.20/tr 76% WR n=50 at $100 size; +$1.83/tr n=31) — genuine go-live
    # contenders, and the regime evidence (fleet_floor_2h blocks 74%-won5/+36%-peak
    # candidates) says the young pond is hot right now.
    "young_probe_light", "young_probe_candidate",
    # young-probe clone wave (2026-06-10): mined from the 81-close family record
    # (74% WR) — uptrend-confirmation thesis, held-out 86%/95% test WR
    "young_probe_stair", "young_probe_baseflow",
    # bad-day microcap family (2026-06-10): the other half of the calendar —
    # rug-screened 50-500k flush/momentum riders (see badday scorecard)
    "badday_flush", "badday_momo",
    # fleet-convex wing (2026-06-11): proven lottery-segment entries with the
    # ELITE payoff curve (tiny TP1 partial, 70% rides) — judged vs parents
    "young_probe_stair_convex", "young_probe_baseflow_convex", "badday_flush_convex",
    # young CAPTURE build (2026-06-12): band probes on the adjacent water the
    # proven lane excludes (thin-liq 69% won10 / mid-age 61% / late-young 50%)
    "young_probe_thinliq", "young_probe_mid", "young_probe_late",
}
API = "https://gracious-inspiration-production.up.railway.app/api/trades?full=1&limit=5000"


def _fetch():
    req = urllib.request.Request(API, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)


def load(cache):
    if cache:
        d = json.load(open(cache))
    else:
        d = _fetch()
        if isinstance(d, dict) and d.get("egress_throttled"):
            # The server downgraded the heavy pull to 200 records (egress budget).
            # Reading that silently produced false day verdicts twice (-$42, -$79).
            # One paced retry, then fail LOUDLY rather than compute on a stub.
            print("egress-throttled response — waiting 70s for budget, retrying once...",
                  file=sys.stderr)
            import time
            time.sleep(70)
            d = _fetch()
            if isinstance(d, dict) and d.get("egress_throttled"):
                sys.exit("ABORT: still egress-throttled — rerun later; do NOT trust a "
                         "200-record pull for a day verdict.")
    if isinstance(d, dict) and d.get("egress_throttled"):
        sys.exit("ABORT: cache file is an egress-throttled stub (200 records) — re-pull.")
    return d if isinstance(d, list) else d.get("trades", [])


def is_candidate(t, buy_strategy_by_key):
    if t.get("bot_id") in CANDIDATE_BOTS:
        return t.get("bot_id")
    # smart_follow sells: attribute via their buy's strategy tag. Keyed by
    # ADDRESS ONLY — tracker bot_id flips None <-> 'baseline_v1' across server
    # restarts, so a (addr, bot_id) key mismatches buys and sells from
    # different eras (only the legacy tracker pipeline carries strategy tags,
    # so address-level attribution is unambiguous).
    k = (t.get("pair_address") or t.get("address") or "").lower()
    strat = str(buy_strategy_by_key.get(k) or "")
    if strat.startswith("smart_follow"):
        return strat   # each tier (k3/k2/solo/convex) judged as its own line
    return None


# ── Walk-forward live set (2026-06-10, AxiS) ─────────────────────────────────
# "Measure the goal as if we ran the profitable bots." The honest version of
# that is WALK-FORWARD: day D's live set = bots whose trailing record was
# ALREADY net-positive before D started (what flipping live would actually
# have run that morning) — NOT whoever happened to win on D (hindsight bias).
LIVE_SET_TRAILING_DAYS = 7
LIVE_SET_MIN_CLOSES = 3      # noise floor: 1 lucky close doesn't make a live bot
LIVE_SET_MIN_NET = 0.0


def build_daily(trades):
    """day -> bot -> {'pnl': $, 'n': closes} over the candidate universe."""
    buy_strat = {}
    for t in trades:
        if t.get("type") == "buy" and t.get("strategy"):
            k = (t.get("pair_address") or t.get("address") or "").lower()
            buy_strat[k] = t.get("strategy")
    daily = {}
    for t in trades:
        if t.get("type") != "sell":
            continue
        if "cancelled on restart" in (t.get("reason") or "").lower():
            continue
        who = is_candidate(t, buy_strat)
        if not who:
            continue
        try:
            dt = datetime.fromisoformat(str(t.get("time")).replace("Z", "+00:00"))
            day = (dt - timedelta(hours=5)).strftime("%Y-%m-%d")
        except Exception:
            continue
        rec = daily.setdefault(day, {}).setdefault(who, {"pnl": 0.0, "n": 0})
        rec["pnl"] += float(t.get("pnl") or 0)
        rec["n"] += 1
    return daily


def _bot_sizes():
    """bot_id -> base_position_usd from configs (for $100-normalized view).
    smart_follow tiers: $50 (k3/k2/solo) / $25 (convex) since the bleed-cut."""
    import glob, os
    sizes = {}
    for f in glob.glob(os.path.join("config", "bots", "*.json")):
        try:
            c = json.load(open(f))
            if c.get("bot_id"):
                sizes[c["bot_id"]] = float(c.get("base_position_usd") or 0) or 100.0
        except Exception:
            pass
    sizes.update({"smart_follow": 50.0, "smart_follow_k2": 50.0,
                  "smart_follow_solo": 50.0, "smart_follow_convex": 25.0})
    return sizes


def live_set_for_day(daily, day):
    """Bots qualified BEFORE `day`: trailing-window net>0 with enough closes."""
    try:
        d0 = datetime.strptime(day, "%Y-%m-%d")
    except Exception:
        return set()
    window = {(d0 - timedelta(days=i)).strftime("%Y-%m-%d")
              for i in range(1, LIVE_SET_TRAILING_DAYS + 1)}
    agg = {}
    for d in window:
        for bot, rec in (daily.get(d) or {}).items():
            a = agg.setdefault(bot, {"pnl": 0.0, "n": 0})
            a["pnl"] += rec["pnl"]
            a["n"] += rec["n"]
    return {b for b, a in agg.items()
            if a["n"] >= LIVE_SET_MIN_CLOSES and a["pnl"] > LIVE_SET_MIN_NET}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None)
    args = ap.parse_args()
    trades = load(args.cache)

    daily = build_daily(trades)

    print(f"GOAL: ${GOAL_USD_PER_DAY:.0f}/day before live production")
    print(f"HEADLINE = walk-forward LIVE SET (bots already net-positive over the "
          f"trailing {LIVE_SET_TRAILING_DAYS}d with >={LIVE_SET_MIN_CLOSES} closes "
          f"BEFORE the day started — what going live would actually have run).")
    print(f"candidate universe: {', '.join(sorted(CANDIDATE_BOTS))} + smart_follow\n")
    print(f"{'day (CT)':12s}{'live-set $':>11s}{'@$100':>9s}{'vs goal':>9s}{'all-cand $':>11s}"
          f"{'ran':>4s}  live set that day")
    days = sorted(daily)
    streak = 0
    sizes = _bot_sizes()
    for day in days[-14:]:
        bots = daily[day]
        full = sum(r["pnl"] for r in bots.values())
        live = live_set_for_day(daily, day)
        live_tot = sum(bots[b]["pnl"] for b in bots if b in live)
        # $100-normalized: what the SAME live-set trades earn at uniform $100
        # positions (paper probes are $10-100; this answers "would going live
        # at real size have made the goal" — AxiS 2026-06-11)
        live_norm = sum(bots[b]["pnl"] * (100.0 / sizes.get(b, 100.0))
                        for b in bots if b in live)
        ok = "MET ✓" if live_norm >= GOAL_USD_PER_DAY else f"{live_norm-GOAL_USD_PER_DAY:+.0f}"
        names = ",".join(sorted(b for b in live if b in bots)) or "(none qualified)"
        print(f"  {day} {live_tot:+11.0f} {live_norm:+9.0f} {ok:>8s} {full:+11.0f} {len(live):4d}  {names[:54]}")
        streak = streak + 1 if live_norm >= GOAL_USD_PER_DAY else 0
    if days:
        print(f"\nconsecutive $100-NORMALIZED live-set days >= ${GOAL_USD_PER_DAY:.0f}: {streak}")
        print("(suggest requiring >=5 consecutive MET days before the go-live conversation)")


if __name__ == "__main__":
    main()
