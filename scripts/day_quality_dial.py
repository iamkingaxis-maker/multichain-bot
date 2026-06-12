"""DAY-QUALITY DIAL — pre-registered forward experiment (registered 2026-06-12).

THE SIGNAL (the tide, not the current): whether ANYTHING works today persists
and is detectable early. First-6h-CT fleet WR predicts rest-of-day fleet P&L:
  - r=+0.52 on the 49-day market-data reconstruction (2026-06-08 study)
  - r=+0.40 independently on our own ledger (23 days, measured 2026-06-12)
  - dial sim on those 23 days: flat -11,365 -> dialed -8,217 (+3,148 saved,
    mostly by HALVING catastrophic days)

REGISTRATION (frozen — no tuning during the window):
  signal   : fleet-wide closed sells 00:00-05:59 CT (all bots; excludes
             'cancelled on restart' and |pnl_pct|>150 phantom class).
             WR = share with pnl>0. If early n<30 -> dial is NEUTRAL (1.0).
  dial     : WR < 0.50 -> 0.5x | WR > 0.65 -> 1.5x | else 1.0x
  applied  : (hypothetically) to all rest-of-day closes (06:00-23:59 CT).
  window   : 2026-06-13 .. 2026-06-26 CT (14 forward days).
  decision : 2026-06-27.
  bar      : cumulative dialed > flat over the window. Track the 0.5x and
             1.5x legs SEPARATELY — upsize rules historically die first, so
             enforcement may be granted to the downsize leg alone.
  enforce  : NOTHING until the bar is met; this script is the whole
             experiment (the ledger already records everything needed).

Usage:
  python scripts/day_quality_dial.py [--cache _trades_cache.json]
"""
from __future__ import annotations
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REGISTRATION = {
    "name": "day_quality_dial",
    "registered": "2026-06-12",
    "window_start_ct": "2026-06-13",
    "window_end_ct": "2026-06-26",
    "decision_date": "2026-06-27",
    "early_hours_ct": [0, 6],
    "min_early_closes": 30,
    "wr_low": 0.50, "mult_low": 0.5,
    "wr_high": 0.65, "mult_high": 1.5,
    "phantom_pct_abs": 150.0,
    "bar": "cumulative dialed > flat over >=14 forward days; legs tracked separately",
}


def dial_mult(wr: float, n: int) -> float:
    if n < REGISTRATION["min_early_closes"]:
        return 1.0
    if wr < REGISTRATION["wr_low"]:
        return REGISTRATION["mult_low"]
    if wr > REGISTRATION["wr_high"]:
        return REGISTRATION["mult_high"]
    return 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="_trades_cache.json")
    ap.add_argument("--all-days", action="store_true",
                    help="include pre-window days (context only)")
    args = ap.parse_args()

    rows = json.load(open(args.cache))
    rows = rows if isinstance(rows, list) else rows.get("trades", [])

    early = defaultdict(lambda: [0, 0])     # day -> [wins, n]
    rest = defaultdict(lambda: [0.0, 0])    # day -> [pnl, n]
    for t in rows:
        if t.get("type") != "sell":
            continue
        if "cancelled on restart" in (t.get("reason") or "").lower():
            continue
        pp = t.get("pnl_pct")
        if isinstance(pp, (int, float)) and abs(pp) > REGISTRATION["phantom_pct_abs"]:
            continue
        try:
            dt = datetime.fromisoformat(str(t.get("time")).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ct = dt - timedelta(hours=5)
        except Exception:
            continue
        d = ct.strftime("%Y-%m-%d")
        pnl = float(t.get("pnl") or 0)
        if REGISTRATION["early_hours_ct"][0] <= ct.hour < REGISTRATION["early_hours_ct"][1]:
            early[d][0] += pnl > 0
            early[d][1] += 1
        else:
            rest[d][0] += pnl
            rest[d][1] += 1

    w0, w1 = REGISTRATION["window_start_ct"], REGISTRATION["window_end_ct"]
    print(f"DAY-QUALITY DIAL — registered {REGISTRATION['registered']}, "
          f"window {w0}..{w1}, decision {REGISTRATION['decision_date']}")
    print(f"dial: first-6h-CT fleet WR <{REGISTRATION['wr_low']:.0%} -> "
          f"{REGISTRATION['mult_low']}x | >{REGISTRATION['wr_high']:.0%} -> "
          f"{REGISTRATION['mult_high']}x | else 1.0 (neutral if early n<"
          f"{REGISTRATION['min_early_closes']})\n")
    print(f"{'day':12s}{'earlyWR':>8s}{'n':>5s}{'mult':>6s}{'rest flat $':>12s}"
          f"{'rest dialed $':>14s}{'delta':>8s}")

    flat_t = dial_t = 0.0
    leg_low = leg_high = 0.0
    days_n = 0
    for d in sorted(rest):
        in_window = w0 <= d <= w1
        if not in_window and not args.all_days:
            continue
        we, ne = early[d]
        wr = we / ne if ne else 0.0
        m = dial_mult(wr, ne)
        rp, rn = rest[d]
        dialed = rp * m
        tag = "" if in_window else "  (pre-window)"
        print(f"{d:12s}{wr:8.0%}{ne:5d}{m:6.1f}{rp:+12.0f}{dialed:+14.0f}"
              f"{dialed - rp:+8.0f}{tag}")
        if in_window:
            flat_t += rp
            dial_t += dialed
            days_n += 1
            if m == REGISTRATION["mult_low"]:
                leg_low += dialed - rp
            elif m == REGISTRATION["mult_high"]:
                leg_high += dialed - rp

    print(f"\nwindow days so far: {days_n}/14")
    if days_n:
        print(f"flat {flat_t:+.0f} | dialed {dial_t:+.0f} | edge {dial_t - flat_t:+.0f}")
        print(f"legs: downsize(0.5x) {leg_low:+.0f} | upsize(1.5x) {leg_high:+.0f}")
        if days_n >= 14:
            print(f"VERDICT vs bar: {'MET' if dial_t > flat_t else 'NOT MET'}")
        else:
            print(f"(verdict at {REGISTRATION['decision_date']}; no peeking-based changes)")


if __name__ == "__main__":
    main()
