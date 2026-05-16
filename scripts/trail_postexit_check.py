"""What happened AFTER we exited via trail?

For each closed trade where exit reason was "Dip trail -X% from peak",
fetch DexScreener candles for the 30 min following the exit time and
compute:

  - max_high_after_exit_pct: highest the token went AFTER our exit
    (measured as pct from our entry price)
  - min_low_after_exit_pct: lowest after our exit
  - drift_30m_pct: where the token was 30 min after our exit

If max_high_after_exit consistently EXCEEDS our exit pct by N pp, the
trail is too tight — wider trail would have captured that move.
If min_low_after_exit is below our exit, we'd have given back gains.

The net gain/loss from loosening the trail = the median of
(max_high - exit_pct) vs (min_low - exit_pct) weighted by which the
wider trail would have caught.

This is more reliable than full-candle simulation because we only need
post-exit prices, and the post-exit window is longer than the (often
candle-sparse) hold window.
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feeds.dexscreener_client import DexScreenerClient

DASHBOARD_URL = "https://gracious-inspiration-production.up.railway.app"
POST_EXIT_WINDOW_S = 30 * 60  # look 30 min after exit


def parse_iso(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00") if "Z" in s else s
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def fetch_trades():
    url = f"{DASHBOARD_URL}/api/trades?limit=1000"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


async def post_exit_high_low(client, pair, exit_ts):
    candles = await client.fetch_1m(pair, limit=200)
    window = [c for c in candles
              if exit_ts < c.open_time <= exit_ts + POST_EXIT_WINDOW_S]
    if not window:
        return None
    # Need price at exit to anchor — use the candle closest to exit_ts (close)
    pre = [c for c in candles if c.open_time <= exit_ts]
    if not pre:
        return None
    anchor = pre[-1].close
    if anchor <= 0:
        return None
    max_h = max(c.high for c in window)
    min_l = min(c.low for c in window)
    last = window[-1].close
    return {
        "max_above_exit_pct": (max_h / anchor - 1) * 100,
        "min_below_exit_pct": (min_l / anchor - 1) * 100,
        "drift_at_window_end_pct": (last / anchor - 1) * 100,
        "n_candles": len(window),
    }


async def main():
    trades = fetch_trades()
    # Trail-exit cohort: reason starts with "Dip trail"
    cutoff = datetime.now(timezone.utc).timestamp() - 7 * 24 * 3600
    cohort = []
    for t in trades:
        if t.get("type") != "sell":
            continue
        if not (t.get("reason") or "").startswith("Dip trail"):
            continue
        dt = parse_iso(t.get("time"))
        if not dt or dt.timestamp() < cutoff:
            continue
        if not t.get("pair_address"):
            continue
        cohort.append(t)
    print(f"Trail-exit trades in last 7d: {len(cohort)}")
    if not cohort:
        return

    client = DexScreenerClient()
    results = []
    for i, t in enumerate(cohort):
        exit_dt = parse_iso(t["time"])
        if not exit_dt:
            continue
        post = await post_exit_high_low(client, t["pair_address"], exit_dt.timestamp())
        if not post:
            continue
        peak = t.get("peak_pnl_pct") or 0
        actual = t.get("pnl_pct") or 0
        results.append({
            "token": t.get("token"),
            "peak": peak,
            "actual": actual,
            "max_above_exit": post["max_above_exit_pct"],
            "min_below_exit": post["min_below_exit_pct"],
            "drift_30m": post["drift_at_window_end_pct"],
        })
        if (i + 1) % 5 == 0:
            print(f"  {i+1}/{len(cohort)} done ...")
        await asyncio.sleep(0.1)

    if not results:
        print("no post-exit data fetchable")
        return

    print(f"\n=== Post-exit price behavior (n={len(results)}) ===")
    print(f"{'Token':<12} {'peak':>6} {'exit':>6} {'max_above':>10} {'min_below':>10} {'drift_30m':>10}")
    for r in sorted(results, key=lambda x: -x["max_above_exit"]):
        print(f"  {r['token']:<12} {r['peak']:>+5.1f}% {r['actual']:>+5.1f}% "
              f"{r['max_above_exit']:>+8.1f}%  {r['min_below_exit']:>+8.1f}% "
              f"{r['drift_30m']:>+8.1f}%")

    # Summary stats
    n = len(results)
    upside_runners = sum(1 for r in results if r["max_above_exit"] > 2.0)
    downside_dumps = sum(1 for r in results if r["min_below_exit"] < -2.0)
    median_max = sorted(r["max_above_exit"] for r in results)[n // 2]
    median_min = sorted(r["min_below_exit"] for r in results)[n // 2]
    median_drift = sorted(r["drift_30m"] for r in results)[n // 2]
    print(f"\nSummary:")
    print(f"  upside_runners (>+2% past exit):  {upside_runners}/{n} ({upside_runners/n*100:.0f}%)")
    print(f"  downside_dumps (>-2% past exit):  {downside_dumps}/{n} ({downside_dumps/n*100:.0f}%)")
    print(f"  median max above exit:  {median_max:+.1f}%")
    print(f"  median min below exit:  {median_min:+.1f}%")
    print(f"  median drift at +30m:   {median_drift:+.1f}%")

    # Estimate trail-loosening gain: for trail-fired trades, if we held longer (wider trail),
    # the difference would be median_max minus what we currently capture.
    # We currently exit at peak - 1pp. If we widened to peak - X pp, we'd hold
    # through bounces up to X pp drawdown. Approximation: gain = avg of
    # (max_above_exit if it's positive AND drift_30m is positive).
    captures_positive = [r["max_above_exit"] for r in results
                         if r["max_above_exit"] > 0 and r["drift_30m"] > 0]
    captures_negative = [r["max_above_exit"] for r in results
                         if r["drift_30m"] < -2.0]
    if captures_positive:
        avg_left = sum(captures_positive) / len(captures_positive)
        print(f"\n  Avg upside left on table (when token continued up): {avg_left:+.1f}%")
    if captures_negative:
        avg_dumped = sum(captures_negative) / len(captures_negative)
        print(f"  Avg max-up before dump (when token then dumped):     {avg_dumped:+.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
