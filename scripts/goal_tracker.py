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
}
API = "https://gracious-inspiration-production.up.railway.app/api/trades?full=1&limit=5000"


def load(cache):
    if cache:
        d = json.load(open(cache))
    else:
        req = urllib.request.Request(API, headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        d = json.loads(raw)
    return d if isinstance(d, list) else d.get("trades", [])


def is_candidate(t, buy_strategy_by_key):
    if t.get("bot_id") in CANDIDATE_BOTS:
        return t.get("bot_id")
    # smart_follow sells: attribute via their buy's strategy tag
    k = ((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
    if buy_strategy_by_key.get(k) == "smart_follow":
        return "smart_follow"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None)
    args = ap.parse_args()
    trades = load(args.cache)

    buy_strat = {}
    for t in trades:
        if t.get("type") == "buy" and t.get("strategy"):
            k = ((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
            buy_strat[k] = t.get("strategy")

    daily = defaultdict(lambda: defaultdict(float))   # day -> bot -> $
    for t in trades:
        if t.get("type") != "sell":
            continue
        if "cancelled on restart" in (t.get("reason") or "").lower():
            continue
        who = is_candidate(t, buy_strat)
        if not who:
            continue
        # CT day boundary (CDT = UTC-5)
        try:
            dt = datetime.fromisoformat(str(t.get("time")).replace("Z", "+00:00"))
            day = (dt - timedelta(hours=5)).strftime("%Y-%m-%d")
        except Exception:
            continue
        daily[day][who] += float(t.get("pnl") or 0)

    print(f"GOAL: ${GOAL_USD_PER_DAY:.0f}/day (closed P&L, candidate set) before live production")
    print(f"candidate set: {', '.join(sorted(CANDIDATE_BOTS))} + smart_follow\n")
    print(f"{'day (CT)':12s}{'cand-set $':>11s}{'vs goal':>9s}  top contributors")
    days = sorted(daily)
    streak = 0
    for day in days[-14:]:
        bots = daily[day]
        tot = sum(bots.values())
        ok = "MET ✓" if tot >= GOAL_USD_PER_DAY else f"{tot-GOAL_USD_PER_DAY:+.0f}"
        top = sorted(bots.items(), key=lambda kv: -kv[1])[:3]
        tops = ", ".join(f"{b}:{v:+.0f}" for b, v in top)
        print(f"  {day} {tot:+11.0f} {ok:>8s}  {tops}")
        streak = streak + 1 if tot >= GOAL_USD_PER_DAY else 0
    if days:
        print(f"\nconsecutive days >= ${GOAL_USD_PER_DAY:.0f}: {streak}")
        print("(suggest requiring >=5 consecutive MET days before the go-live conversation)")


if __name__ == "__main__":
    main()
