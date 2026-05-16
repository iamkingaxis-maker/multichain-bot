"""Validate Path A (calm_shallow trigger) + Path B (fomo_peak filter)
against our actual paired trade cohort + the universe data.

For Path A — trigger fires when:
    buys_h1 <= 735 AND cum_pct_5m >= -7.94

For Path B — filter blocks when:
    buys_h1 >= ??? AND cum_pct_5m <= ???
We sweep to find the highest-precision (most dyer-concentrated) cut.

Report:
  - Universe-data cohort sizes + WR for each path
  - Live-trade match-rate: how many of our last 14d entries fall in each
    cohort, and what their actual WR / P&L looks like
  - Estimated impact on bot:
    - throughput delta (blocked entries vs triggered upsize)
    - WR delta (estimated from cohort survivor rates)
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DASHBOARD_URL = "https://gracious-inspiration-production.up.railway.app"
UNIVERSE_PATH = Path("universe_fresh.json")


def parse_iso(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00") if "Z" in s else s
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def main():
    # ── 1. Universe-side analysis ────────────────────────────────────
    events = json.loads(UNIVERSE_PATH.read_text())
    print(f"Universe events: {len(events)}")

    def survivor(e):
        return (isinstance(e.get("peak_pct"), (int, float))
                and e["peak_pct"] >= 10.0
                and isinstance(e.get("exit_pct"), (int, float))
                and e["exit_pct"] >= 0)

    def dyer(e):
        return (isinstance(e.get("exit_pct"), (int, float))
                and e["exit_pct"] <= -20.0)

    # Path A: buys_h1 <= 735 AND cum_pct_5m >= -7.94
    def path_a(e):
        b = e.get("buys_h1"); c = e.get("cum_pct_5m")
        if not isinstance(b, (int, float)) or not isinstance(c, (int, float)):
            return False
        return b <= 735 and c >= -7.94

    # Path B: sweep for the most-dyer-rich cut
    print("\n=== Sweeping Path B (FOMO-peak filter) on universe ===")
    print(f"  {'buys_h1_min':>12} {'cum_pct_5m_max':>16} {'n':>5} {'surv%':>6} {'dyer%':>6} {'block_action':>14}")
    candidates = []
    # Goal: find cut where dyer% is high (>50%) and sample is meaningful (n>=30)
    for buys_min in (1500, 2000, 2500, 3000, 4000, 5000):
        for cum_max in (-7.94, -10, -12, -15, -18):
            matched = [e for e in events
                       if isinstance(e.get("buys_h1"), (int, float)) and e["buys_h1"] >= buys_min
                       and isinstance(e.get("cum_pct_5m"), (int, float)) and e["cum_pct_5m"] <= cum_max]
            n = len(matched)
            if n < 30:
                continue
            survs = sum(1 for e in matched if survivor(e))
            dyrs = sum(1 for e in matched if dyer(e))
            evaluable = survs + dyrs
            if evaluable < 15:
                continue
            sp = survs / evaluable * 100
            dp = dyrs / evaluable * 100
            note = "GOOD-BLOCK" if dp >= 60 else ("MARGINAL" if dp >= 50 else "")
            candidates.append({
                "buys_min": buys_min, "cum_max": cum_max,
                "n": n, "surv_pct": sp, "dyer_pct": dp, "note": note,
            })
            print(f"  {buys_min:>12} {cum_max:>16} {n:>5} {sp:>5.0f}% {dp:>5.0f}%  {note:>13}")

    # Pick the best Path B candidate: highest dyer% with n>=30
    valid = [c for c in candidates if c["note"] == "GOOD-BLOCK"]
    if not valid:
        valid = [c for c in candidates if c["note"] == "MARGINAL"]
    if not valid:
        print("\n  ⚠ No clean Path B cut found — fomo_peak filter may not be viable")
        path_b_cut = None
    else:
        valid.sort(key=lambda c: -c["dyer_pct"])
        path_b_cut = valid[0]
        print(f"\n  → Selected Path B cut: buys_h1 >= {path_b_cut['buys_min']} "
              f"AND cum_pct_5m <= {path_b_cut['cum_max']} "
              f"(n={path_b_cut['n']}, dyer% = {path_b_cut['dyer_pct']:.0f}%)")

    def path_b(e, cut=path_b_cut):
        if not cut:
            return False
        b = e.get("buys_h1"); c = e.get("cum_pct_5m")
        if not isinstance(b, (int, float)) or not isinstance(c, (int, float)):
            return False
        return b >= cut["buys_min"] and c <= cut["cum_max"]

    # Path A and Path B universe baseline
    base_surv = sum(1 for e in events if survivor(e))
    base_dyer = sum(1 for e in events if dyer(e))
    base_eval = base_surv + base_dyer
    print(f"\nUniverse baseline: surv={base_surv}/{base_eval} ({base_surv/base_eval*100:.0f}%)")
    a_match = [e for e in events if path_a(e)]
    a_surv = sum(1 for e in a_match if survivor(e))
    a_dyer = sum(1 for e in a_match if dyer(e))
    print(f"Path A (calm_shallow) matches: {len(a_match)}/{len(events)} "
          f"({len(a_match)/len(events)*100:.0f}%) "
          f"— surv={a_surv}/{a_surv+a_dyer} ({a_surv/max(a_surv+a_dyer,1)*100:.0f}%)")
    if path_b_cut:
        b_match = [e for e in events if path_b(e)]
        b_surv = sum(1 for e in b_match if survivor(e))
        b_dyer = sum(1 for e in b_match if dyer(e))
        print(f"Path B (fomo_peak) matches:    {len(b_match)}/{len(events)} "
              f"({len(b_match)/len(events)*100:.0f}%) "
              f"— surv={b_surv}/{b_surv+b_dyer} ({b_surv/max(b_surv+b_dyer,1)*100:.0f}%)")

    # ── 2. Live trade cohort cross-reference ─────────────────────────
    print(f"\n=== Cross-ref on our actual trades (last 14d) ===")
    with urllib.request.urlopen(f"{DASHBOARD_URL}/api/trades?limit=2000", timeout=30) as r:
        trades = json.loads(r.read())

    # Pair buys with sells
    by_key = {}
    for t in trades:
        if t.get("strategy") not in ("dip_buy", "scanner"):
            continue
        key = (t.get("token"), round(t.get("entry_price", 0), 10))
        by_key.setdefault(key, []).append(t)
    pairs = []
    cutoff = datetime.now(timezone.utc).timestamp() - 14 * 24 * 3600
    for key, events_ in by_key.items():
        buys_ = [e for e in events_ if e.get("type") == "buy"]
        sells_ = [e for e in events_ if e.get("type") == "sell"]
        if not buys_ or not sells_:
            continue
        buy = buys_[0]
        dt = parse_iso(buy.get("time"))
        if not dt or dt.timestamp() < cutoff:
            continue
        sells_.sort(key=lambda x: x.get("time", ""))
        last = sells_[-1]
        # Aggregate pnl across all legs (sum of pnl $; pnl_pct from last leg)
        # Use last-leg pnl_pct since it's the position-aggregate
        em = buy.get("entry_meta") or {}
        pairs.append({
            "token": key[0],
            "actual_pnl_pct": last.get("pnl_pct") or 0,
            "peak_pnl_pct": last.get("peak_pnl_pct") or 0,
            "won": (last.get("pnl_pct") or 0) > 0,
            "big_win": (last.get("peak_pnl_pct") or 0) >= 5.0 and (last.get("pnl_pct") or 0) > 0,
            "buys_h1": em.get("buys_h1"),
            "cum_pct_5m": em.get("cum_pct_5m") or em.get("pc_m5"),
            "reason": last.get("reason", ""),
        })

    print(f"Paired trades (14d): {len(pairs)}")
    # Path A matches in our cohort
    def live_a(p):
        b = p["buys_h1"]; c = p["cum_pct_5m"]
        if not isinstance(b, (int, float)) or not isinstance(c, (int, float)):
            return False
        return b <= 735 and c >= -7.94

    def live_b(p, cut=path_b_cut):
        if not cut:
            return False
        b = p["buys_h1"]; c = p["cum_pct_5m"]
        if not isinstance(b, (int, float)) or not isinstance(c, (int, float)):
            return False
        return b >= cut["buys_min"] and c <= cut["cum_max"]

    a_hits = [p for p in pairs if live_a(p)]
    b_hits = [p for p in pairs if live_b(p)]
    has_data = [p for p in pairs
                if isinstance(p["buys_h1"], (int, float))
                and isinstance(p["cum_pct_5m"], (int, float))]
    print(f"  with both features populated: {len(has_data)}/{len(pairs)}")

    def stats(label, cohort):
        if not cohort:
            print(f"  {label:<28} n=0")
            return
        n = len(cohort)
        wins = sum(1 for p in cohort if p["won"])
        big_wins = sum(1 for p in cohort if p["big_win"])
        pnl_sum = sum(p["actual_pnl_pct"] for p in cohort)
        print(f"  {label:<28} n={n:>3}  WR={wins/n*100:>3.0f}%  "
              f"big_wins={big_wins:>2}  total_pnl_pct={pnl_sum:>+6.1f}  avg/trade={pnl_sum/n:>+5.2f}%")

    stats("ALL paired 14d:", pairs)
    stats("Has both features:", has_data)
    stats("Path A fires (calm_shallow):", a_hits)
    if path_b_cut:
        stats("Path B fires (fomo_peak BLOCK):", b_hits)
    not_a = [p for p in has_data if not live_a(p)]
    stats("Has features AND NOT Path A:", not_a)
    not_b = [p for p in has_data if not live_b(p)]
    if path_b_cut:
        stats("Has features AND NOT Path B:", not_b)
        # Combined: enforce B (drop Path B fires), upsize A
        combined_kept = [p for p in has_data if not live_b(p)]
        stats("AFTER Path B filter:", combined_kept)

    # ── 3. Volume + WR projection ────────────────────────────────────
    if pairs and has_data:
        print(f"\n=== Projected impact (extrapolated from cohort) ===")
        pop_rate = len(has_data) / len(pairs)
        a_rate = len(a_hits) / len(has_data) if has_data else 0
        b_rate = len(b_hits) / len(has_data) if has_data else 0
        print(f"  Feature coverage in trades: {pop_rate*100:.0f}% (features populated)")
        print(f"  Path A fire rate (of populated): {a_rate*100:.0f}%")
        if path_b_cut:
            print(f"  Path B fire rate (of populated): {b_rate*100:.0f}%")
        if path_b_cut and len(b_hits):
            blocked_pnl = sum(p["actual_pnl_pct"] for p in b_hits)
            kept_pnl = sum(p["actual_pnl_pct"] for p in pairs if p not in b_hits)
            current_pnl = sum(p["actual_pnl_pct"] for p in pairs)
            print(f"  CURRENT total pnl: {current_pnl:+.1f}%")
            print(f"  Path B would block {len(b_hits)} trades with total {blocked_pnl:+.1f}%")
            print(f"  → Remaining pnl after Path B: {kept_pnl:+.1f}% "
                  f"(delta: {kept_pnl - current_pnl:+.1f}%)")
            print(f"  → Volume reduction: -{len(b_hits)}/{len(pairs)} = -{len(b_hits)/len(pairs)*100:.0f}%")


if __name__ == "__main__":
    main()
