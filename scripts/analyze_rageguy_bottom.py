"""Pull RAGEGUY 1s bars from DexScreener around the 03:05-03:30 UTC window.

Goal: identify what the actual bottom looked like (post-stop pump),
contrast with what the bot saw at 03:05 entry, and surface 1s features
that could catch real bottoms vs falling knives.

Entry: 2026-05-15 03:05:20 UTC at $0.004541
Stop:  2026-05-15 03:26:36 UTC at $0.004161 (-8.5%)
User: token bottomed AFTER the stop, then pumped
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from curl_cffi import requests as cf_requests

from feeds.dexscreener_chart_format import parse_chart_bars

PAIR = "6gTQBJBV7DUQUGfsQoxzqi8Kgpa5ymF5riquo1k9sXoe"
URL = f"https://io.dexscreener.com/dex/chart/amm/v3/pumpfundex/bars/solana/{PAIR}"

ENTRY_TS = int(datetime(2026, 5, 15, 3, 5, 20, tzinfo=timezone.utc).timestamp())
STOP_TS = int(datetime(2026, 5, 15, 3, 26, 36, tzinfo=timezone.utc).timestamp())


def fetch(res, hours_back=2, hours_fwd=0):
    now = time.time()
    params = {
        "from": int((now - hours_back * 3600) * 1000),
        "to": int((now + hours_fwd * 3600) * 1000),
        "res": str(res),
        "cb": "300",
        "q": "1",
    }
    r = cf_requests.get(URL, params=params, impersonate="chrome", timeout=20)
    return parse_chart_bars(r.content)


def main():
    print(f"RAGEGUY 1s analysis")
    print(f"  pair: {PAIR}")
    print(f"  entry: 03:05:20 UTC at $0.004541")
    print(f"  stop:  03:26:36 UTC at $0.004161 (-8.5%)")
    print()

    # Try 1s first
    bars_1s = fetch(1, hours_back=2)
    print(f"1s bars returned: {len(bars_1s)}")
    if bars_1s:
        print(f"  span: {datetime.fromtimestamp(bars_1s[0]['ts_ms']/1000, tz=timezone.utc)} -> "
              f"{datetime.fromtimestamp(bars_1s[-1]['ts_ms']/1000, tz=timezone.utc)}")

    # Fall back to 5s, 1m as available
    bars_5s = fetch(5, hours_back=3) if not bars_1s or len(bars_1s) < 100 else []
    if bars_5s:
        print(f"5s bars returned: {len(bars_5s)}")

    bars_1m = fetch(1, hours_back=24) if False else None  # 1m default
    bars_use = bars_1s if bars_1s and len(bars_1s) >= 50 else bars_5s
    if not bars_use:
        print("FATAL: no bars")
        return

    # Find entry and stop bar indexes
    def find_idx(target_ts):
        best, best_dt = -1, 1e9
        for i, b in enumerate(bars_use):
            dt_ = abs(b['ts_ms']/1000 - target_ts)
            if dt_ < best_dt:
                best_dt = dt_
                best = i
        return best, best_dt

    entry_idx, entry_dt = find_idx(ENTRY_TS)
    stop_idx, stop_dt = find_idx(STOP_TS)
    print(f"\nentry bar idx={entry_idx} (Δ {entry_dt:.0f}s from target)")
    print(f"stop  bar idx={stop_idx} (Δ {stop_dt:.0f}s from target)")

    # Find the absolute low in the post-stop window (next 30 minutes)
    post = [b for b in bars_use if b['ts_ms']/1000 > STOP_TS]
    if post:
        bottom = min(post, key=lambda b: b['low'])
        peak_after = max(post, key=lambda b: b['high'])
        bottom_ts = datetime.fromtimestamp(bottom['ts_ms']/1000, tz=timezone.utc)
        peak_ts = datetime.fromtimestamp(peak_after['ts_ms']/1000, tz=timezone.utc)
        bottom_pct = (bottom['low'] - 0.004541) / 0.004541 * 100
        peak_pct = (peak_after['high'] - 0.004541) / 0.004541 * 100
        print(f"\nPOST-STOP window (n={len(post)} bars):")
        print(f"  ABSOLUTE LOW:  {bottom['low']:.6f} at {bottom_ts.strftime('%H:%M:%S')} UTC ({bottom_pct:+.1f}% from entry)")
        print(f"  ABSOLUTE HIGH: {peak_after['high']:.6f} at {peak_ts.strftime('%H:%M:%S')} UTC ({peak_pct:+.1f}% from entry)")
        # Min hold required to catch the high
        hold_to_high = peak_after['ts_ms']/1000 - ENTRY_TS
        print(f"  Hold from entry to peak: {hold_to_high:.0f}s ({hold_to_high/60:.1f} min)")

    # Dump the bars in the critical window: entry-2min to stop+15min
    crit = [b for b in bars_use if (ENTRY_TS - 120) < b['ts_ms']/1000 < (STOP_TS + 900)]
    print(f"\n=== CRITICAL WINDOW: 03:03 -> 03:42 UTC ({len(crit)} bars) ===")
    print(f"{'utc':>10s} {'open':>11s} {'close':>11s} {'low':>11s} {'high':>11s} {'body%':>8s} {'vol_usd':>10s} {'note':<20s}")
    for b in crit:
        utc = datetime.fromtimestamp(b['ts_ms']/1000, tz=timezone.utc).strftime('%H:%M:%S')
        body_pct = (b['close']-b['open'])/b['open']*100 if b['open'] else 0
        notes = []
        if abs(b['ts_ms']/1000 - ENTRY_TS) < 1: notes.append('ENTRY')
        if abs(b['ts_ms']/1000 - STOP_TS) < 1: notes.append('STOP')
        if post and b['ts_ms'] == min(post, key=lambda x: x['low'])['ts_ms']:
            notes.append('BOTTOM')
        note = ','.join(notes)
        print(f"  {utc} {b['open']:>11.6f} {b['close']:>11.6f} {b['low']:>11.6f} {b['high']:>11.6f} "
              f"{body_pct:>+7.2f}% {b['volume_usd']:>9.0f} {note:<20s}")

    # Save raw bars for downstream
    with open('.rageguy_1s.json', 'w') as f:
        json.dump([{
            'ts_ms': b['ts_ms'],
            'o': b['open'], 'h': b['high'], 'l': b['low'], 'c': b['close'],
            'v': b['volume_usd']
        } for b in bars_use], f)
    print(f"\nSaved {len(bars_use)} bars to .rageguy_1s.json")


if __name__ == '__main__':
    main()
