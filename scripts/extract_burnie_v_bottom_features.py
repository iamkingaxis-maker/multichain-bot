"""Extract 1s features at the BURNIE 15:52:45 CT V-bottom (true 1s data).

This is the ground-truth feature snapshot — the bar BEFORE the bottom +
the bottom bar + the 5 post-bottom bars. These features define the
threshold for a cascade-bottom trigger.
"""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def features_at(bars, idx):
    if idx < 1:
        return {}
    bar = bars[idx]
    t_now = bar["ts_ms"]
    win60 = [b for b in bars[:idx + 1] if t_now - b["ts_ms"] <= 60_000]
    win30 = [b for b in bars[:idx + 1] if t_now - b["ts_ms"] <= 30_000]
    win120 = [b for b in bars[:idx + 1] if t_now - b["ts_ms"] <= 120_000]
    if not win60:
        return {}
    cum_60s_pct = (bar["close"] - win60[0]["open"]) / win60[0]["open"] * 100 if win60[0]["open"] else 0
    cum_30s_pct = (bar["close"] - win30[0]["open"]) / win30[0]["open"] * 100 if win30 and win30[0]["open"] else 0
    cum_120s_pct = (bar["close"] - win120[0]["open"]) / win120[0]["open"] * 100 if win120 and win120[0]["open"] else 0
    red_60 = sum(1 for b in win60 if b["close"] < b["open"])
    cascade = 0
    for b in reversed(win60):
        if b["close"] < b["open"]:
            cascade += 1
        else:
            break
    hi_60 = max(b["high"] for b in win60)
    lo_60 = min(b["low"] for b in win60)
    close_pos = (bar["close"] - lo_60) / (hi_60 - lo_60) if hi_60 > lo_60 else 0.5
    range_60_pct = (hi_60 - lo_60) / lo_60 * 100 if lo_60 else 0
    dd_60_pct = (hi_60 - bar["low"]) / hi_60 * 100 if hi_60 else 0
    body = abs(bar["close"] - bar["open"])
    body_low = min(bar["open"], bar["close"])
    body_high = max(bar["open"], bar["close"])
    lower_wick = body_low - bar["low"]
    total_range = bar["high"] - bar["low"]
    lwr = lower_wick / total_range if total_range > 0 else 0
    body_pct = (bar["close"] - bar["open"]) / bar["open"] * 100 if bar["open"] else 0
    # Volume burst
    vol_30 = sum(b["volume_usd"] for b in win30)
    vol_prior60 = sum(b["volume_usd"] for b in win120) - vol_30
    avg_baseline_per_30 = vol_prior60 / 3.0 if vol_prior60 else 0
    vol_burst_30s = vol_30 / avg_baseline_per_30 if avg_baseline_per_30 > 0 else 0
    return {
        "ts_ms": bar["ts_ms"],
        "low": bar["low"],
        "close": bar["close"],
        "cum_30s_pct": cum_30s_pct,
        "cum_60s_pct": cum_60s_pct,
        "cum_120s_pct": cum_120s_pct,
        "red_60": red_60,
        "cascade": cascade,
        "close_pos_60s": close_pos,
        "range_60_pct": range_60_pct,
        "drawdown_60s_pct": dd_60_pct,
        "lwr": lwr,
        "body_pct": body_pct,
        "vol_burst_30s": vol_burst_30s,
        "bar_vol_usd": bar["volume_usd"],
    }


def main():
    bars = json.load(open(".burnie_live_1s.json"))
    print(f"Loaded {len(bars)} 1s bars")

    # Bottom is at 15:52:45 CT — find its index
    target_ts = int(datetime(2026, 5, 14, 15, 52, 45, tzinfo=ZoneInfo("America/Chicago")).timestamp() * 1000)
    bot_idx = min(range(len(bars)), key=lambda i: abs(bars[i]["ts_ms"] - target_ts))
    bot = bars[bot_idx]
    bot_t = datetime.fromtimestamp(bot["ts_ms"] / 1000, tz=timezone.utc).astimezone(ZoneInfo("America/Chicago"))
    print(f"Found bottom at idx={bot_idx} {bot_t.strftime('%H:%M:%S CT')} low={bot['low']:.2f}")
    print()

    print("Features at bottom and surrounding bars:")
    print(f"{'idx':>4} {'time':10s} {'close':>8s} {'cum30':>7s} {'cum60':>7s} {'cum120':>8s} "
          f"{'red60':>5s} {'casc':>4s} {'cpos':>5s} {'rng60':>6s} {'dd60':>6s} "
          f"{'lwr':>5s} {'body%':>7s} {'vbst':>6s} {'vol$':>7s}")
    print("-" * 120)
    for j in range(max(0, bot_idx - 8), min(len(bars), bot_idx + 6)):
        f = features_at(bars, j)
        if not f:
            continue
        bar = bars[j]
        t = datetime.fromtimestamp(bar["ts_ms"] / 1000, tz=timezone.utc).astimezone(ZoneInfo("America/Chicago"))
        mark = ' <-BOT' if j == bot_idx else (' (post)' if j > bot_idx else '')
        print(f"{j:>4} {t.strftime('%H:%M:%S'):10s} {bar['close']:>8.2f} "
              f"{f['cum_30s_pct']:>+6.2f}% {f['cum_60s_pct']:>+6.2f}% {f['cum_120s_pct']:>+7.2f}% "
              f"{f['red_60']:>5d} {f['cascade']:>4d} {f['close_pos_60s']:>5.2f} "
              f"{f['range_60_pct']:>5.2f}% {f['drawdown_60s_pct']:>5.2f}% "
              f"{f['lwr']:>5.2f} {f['body_pct']:>+6.2f}% "
              f"{f['vol_burst_30s']:>5.1f}x ${f['bar_vol_usd']:>5.0f}{mark}")
    print()
    # Save the snapshot at the entry-candidate ticks
    snapshot = [features_at(bars, j) for j in range(bot_idx, min(len(bars), bot_idx + 5))]
    json.dump(snapshot, open(".burnie_vbottom_features.json", "w"), indent=2, default=float)
    print("Saved entry-candidate features to .burnie_vbottom_features.json")


if __name__ == "__main__":
    main()
