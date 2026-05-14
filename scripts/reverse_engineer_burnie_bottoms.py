"""Reverse-engineer the trigger threshold from BURNIE's 3 morning V-bottoms
using 1m bars (DexScreener doesn't expose 1s data from this morning anymore).

The 1m features mirror what the bot's pipeline actually uses for entry:
  1m_last_close_pct, 1m_volume_spike, 1m_cum_3min_pct, 1m_consec_red,
  1m_lower_wick_ratio (derivable), etc.

For each bottom, look at the bottom bar + the next 3 bars (entries would
fire at-or-after the bottom), compute candidate features, then identify
thresholds that catch all 3.
"""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))
from curl_cffi import requests as cf_requests

from feeds.dexscreener_chart_format import parse_chart_bars

PAIR = "5tYFviFWQRKV9BJSTHGitbdqEYC1BGUgRUDnSADUXqJP"
URL = f"https://io.dexscreener.com/dex/chart/amm/v3/pumpfundex/bars/solana/{PAIR}"

BOTTOMS = [
    {"name": "B1_04:10", "ct": (2026, 5, 14, 4, 10), "expected_low": 9153},
    {"name": "B2_05:25", "ct": (2026, 5, 14, 5, 25), "expected_low": 8892},
    {"name": "B3_07:55", "ct": (2026, 5, 14, 7, 55), "expected_low": 8996},
]


def fetch_full_day():
    """Pull 5m bars for the full day — coarser but reaches all the way back."""
    import datetime as dt
    now_ms = int(time.time() * 1000)
    params = {
        "from": int((time.time() - 24 * 3600) * 1000),
        "to": now_ms,
        "res": "5",
        "cb": "300",
        "q": "1",
    }
    r = cf_requests.get(URL, params=params, impersonate="chrome", timeout=20)
    return parse_chart_bars(r.content)


def features_at(bars, idx):
    """Compute 1m-equivalent features mirroring the bot's 1m_* features."""
    if idx < 1 or not bars:
        return {}
    bar = bars[idx]
    # 3-min cumulative (most recent 3 bars)
    win3 = bars[max(0, idx - 2):idx + 1]
    cum_3min = (bar["close"] - win3[0]["open"]) / win3[0]["open"] * 100 if win3[0]["open"] else 0
    # 5-min cumulative
    win5 = bars[max(0, idx - 4):idx + 1]
    cum_5min = (bar["close"] - win5[0]["open"]) / win5[0]["open"] * 100 if win5[0]["open"] else 0
    # Single-bar body
    body_pct = (bar["close"] - bar["open"]) / bar["open"] * 100 if bar["open"] else 0
    # Wick ratios
    total_range = bar["high"] - bar["low"]
    body_low = min(bar["open"], bar["close"])
    body_high = max(bar["open"], bar["close"])
    lower_wick = body_low - bar["low"]
    upper_wick = bar["high"] - body_high
    lwr = lower_wick / total_range if total_range > 0 else 0
    uwr = upper_wick / total_range if total_range > 0 else 0
    # Consecutive reds ending at idx
    consec_red = 0
    for b in reversed(bars[:idx + 1]):
        if b["close"] < b["open"]:
            consec_red += 1
        else:
            break
    # Volume spike: this bar vs trailing 20-bar avg
    win20 = bars[max(0, idx - 20):idx]
    if win20:
        avg_vol = sum(b["volume_usd"] for b in win20) / len(win20)
        vol_spike = bar["volume_usd"] / avg_vol if avg_vol > 0 else 0
    else:
        vol_spike = 0
    # Drawdown from 10-bar peak
    win10 = bars[max(0, idx - 9):idx + 1]
    peak_10 = max(b["high"] for b in win10)
    dd_10 = (peak_10 - bar["low"]) / peak_10 * 100 if peak_10 else 0
    # Close position in 5-bar range
    if win5:
        hi5 = max(b["high"] for b in win5)
        lo5 = min(b["low"] for b in win5)
        cpos5 = (bar["close"] - lo5) / (hi5 - lo5) if hi5 > lo5 else 0.5
    else:
        cpos5 = 0.5
    return {
        "cum_3min_pct": cum_3min,
        "cum_5min_pct": cum_5min,
        "body_pct": body_pct,
        "lwr": lwr,
        "uwr": uwr,
        "consec_red": consec_red,
        "vol_spike_20": vol_spike,
        "drawdown_10_pct": dd_10,
        "close_pos_5": cpos5,
        "bar_vol_usd": bar["volume_usd"],
    }


def find_bottom_near(bars, anchor_ts_ms, window_min=10):
    """Find the bar with the lowest low within ±window_min of anchor_ts."""
    best_idx = -1
    best_low = float("inf")
    for i, b in enumerate(bars):
        delta_min = abs(b["ts_ms"] - anchor_ts_ms) / 60_000
        if delta_min > window_min:
            continue
        if b["low"] < best_low:
            best_low = b["low"]
            best_idx = i
    return best_idx


def main():
    print("Fetching BURNIE 5m bars for the last 24h (coarser; 1m doesn't reach back to morning)...")
    bars = fetch_full_day()
    if not bars:
        print("  no data")
        return
    t0 = datetime.fromtimestamp(bars[0]["ts_ms"] / 1000, tz=timezone.utc).astimezone(ZoneInfo("America/Chicago"))
    t1 = datetime.fromtimestamp(bars[-1]["ts_ms"] / 1000, tz=timezone.utc).astimezone(ZoneInfo("America/Chicago"))
    print(f"  {len(bars)} 5m bars covering {t0.strftime('%m-%d %H:%M CT')} -> {t1.strftime('%m-%d %H:%M CT')}")

    all_results = []
    for spec in BOTTOMS:
        print(f"\n{'=' * 100}")
        print(f"== {spec['name']}  anchor={spec['ct'][3]:02d}:{spec['ct'][4]:02d} CT  "
              f"expected_low={spec['expected_low']}")
        print(f"{'=' * 100}")
        anchor_dt = datetime(*spec["ct"], tzinfo=ZoneInfo("America/Chicago"))
        anchor_ts_ms = int(anchor_dt.timestamp() * 1000)
        bot_idx = find_bottom_near(bars, anchor_ts_ms, window_min=15)
        if bot_idx < 0:
            print("  bottom not found in data")
            continue
        bot = bars[bot_idx]
        bot_t = datetime.fromtimestamp(bot["ts_ms"] / 1000, tz=timezone.utc).astimezone(ZoneInfo("America/Chicago"))
        print(f"  bottom bar: idx={bot_idx} @ {bot_t.strftime('%H:%M:%S CT')} "
              f"low={bot['low']:.2f} close={bot['close']:.2f}")
        print()
        print(f"  {'idx':>4} {'time':8s} {'open':>8s} {'high':>8s} {'low':>8s} {'close':>8s} "
              f"{'cum3m':>7s} {'cum5m':>7s} {'body%':>7s} {'lwr':>5s} {'redN':>4s} "
              f"{'volsp':>6s} {'dd10':>6s} {'cpos5':>5s}")
        rows = []
        for j in range(max(0, bot_idx - 3), min(len(bars), bot_idx + 6)):
            f = features_at(bars, j)
            if not f:
                continue
            bar = bars[j]
            t = datetime.fromtimestamp(bar["ts_ms"] / 1000, tz=timezone.utc).astimezone(ZoneInfo("America/Chicago"))
            marker = " <-BOT" if j == bot_idx else (" (post)" if j > bot_idx else " (pre)")
            print(f"  {j:>4} {t.strftime('%H:%M:%S'):8s} "
                  f"{bar['open']:>8.2f} {bar['high']:>8.2f} "
                  f"{bar['low']:>8.2f} {bar['close']:>8.2f} "
                  f"{f['cum_3min_pct']:>+6.2f}% {f['cum_5min_pct']:>+6.2f}% "
                  f"{f['body_pct']:>+6.2f}% {f['lwr']:>5.2f} {f['consec_red']:>4d} "
                  f"{f['vol_spike_20']:>5.1f}x {f['drawdown_10_pct']:>5.1f}% "
                  f"{f['close_pos_5']:>5.2f}{marker}")
            rows.append({"idx": j, "is_bot": j == bot_idx, "is_post": j > bot_idx, **f})
        all_results.append({"name": spec["name"], "rows": rows, "bot_idx": bot_idx})

    if len(all_results) < 3:
        print("\nIncomplete — skipping synthesis.")
        return

    print(f"\n\n{'=' * 100}")
    print("== THRESHOLD SYNTHESIS  (entry would fire at bottom-bar OR next bar)")
    print(f"{'=' * 100}")
    # For each bottom, find the most-favorable feature signature in the first 2 bars
    # (bottom bar + 1 post-bar — the bot evaluates fresh data each cycle).
    print()
    print(f"  {'feature':18s} " + " ".join(f"{r['name']:>26s}" for r in all_results))
    print(f"  {'':18s} " + " ".join(f"{'(bot, post1, post2)':>26s}" for r in all_results))
    feats = ["cum_3min_pct", "cum_5min_pct", "body_pct", "lwr", "consec_red",
             "vol_spike_20", "drawdown_10_pct", "close_pos_5", "bar_vol_usd"]
    for ft in feats:
        cells = []
        for r in all_results:
            bot_row = next((row for row in r["rows"] if row["is_bot"]), None)
            post_rows = [row for row in r["rows"] if row["is_post"]][:2]
            sample = ([bot_row] + post_rows) if bot_row else post_rows
            vals = [row.get(ft) for row in sample if row.get(ft) is not None]
            if vals:
                triplet = ", ".join(f"{v:+.2f}" for v in vals)
                cells.append(triplet)
            else:
                cells.append("--")
        print(f"  {ft:18s} " + " ".join(f"{c:>26s}" for c in cells))

    print()
    print("Threshold guidance: pick a threshold the WORST-CASE value in each row")
    print("still satisfies, so all 3 bottoms produce a fire.")


if __name__ == "__main__":
    main()
