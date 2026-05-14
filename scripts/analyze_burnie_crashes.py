"""Analyze BURNIE 1s chart — identify crash bottoms + characterize them.

User flagged: huge crashes 4:10-4:40, 5:28-7:30, 7:50-9:00 on the
BURNIE chart with the 1s filter. Says "bottoms of those crashes are
perfect for buying."

Goal: find every cascade-then-reversal event in the chart, characterize
the 1s features at the actual bottom tick, and see if any existing
trigger predicate would have caught those bottoms.
"""
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from curl_cffi import requests as cf_requests

from feeds.dexscreener_chart_format import parse_chart_bars

PAIR = "5tYFviFWQRKV9BJSTHGitbdqEYC1BGUgRUDnSADUXqJP"
CHAIN = "solana"
URL = f"https://io.dexscreener.com/dex/chart/amm/v3/pumpfundex/bars/{CHAIN}/{PAIR}"


def fetch_window(t_from_ms, t_to_ms, res="1S"):
    params = {
        "from": int(t_from_ms),
        "to": int(t_to_ms),
        "res": res,
        "cb": "300",
        "q": "1",
    }
    r = cf_requests.get(URL, params=params, impersonate="chrome", timeout=20)
    if r.status_code != 200:
        return []
    return parse_chart_bars(r.content)


def fetch_all_1s():
    """Walk backwards in time pulling 300-bar windows until we have ~entire
    post-entry history."""
    now_ms = int(time.time() * 1000)
    all_bars = []
    seen = set()
    # Try 7 windows of ~12 minutes each to cover ~80 min total
    # (cb=300 = up to 300 bars; with gaps, each call covers more wall-clock)
    cursor_to = now_ms
    for i in range(10):
        bars = fetch_window(cursor_to - 30 * 60 * 1000, cursor_to)
        if not bars:
            break
        new = [b for b in bars if b['ts_ms'] not in seen]
        for b in new:
            seen.add(b['ts_ms'])
        all_bars.extend(new)
        oldest_ts = min(b['ts_ms'] for b in bars)
        if oldest_ts >= cursor_to - 30 * 60 * 1000 + 1000:
            # Window not exhausted
            break
        cursor_to = oldest_ts
        if cursor_to < now_ms - 6 * 3600 * 1000:
            break
    all_bars.sort(key=lambda b: b['ts_ms'])
    return all_bars


def find_crashes(bars, min_drop_pct=8.0, max_duration_s=120):
    """Find windows where price dropped >= min_drop_pct within max_duration_s.

    Returns list of crash events: (start_idx, bottom_idx, recovery_idx, drop_pct).
    """
    crashes = []
    i = 0
    while i < len(bars):
        peak_price = bars[i]['high']
        peak_idx = i
        # Walk forward looking for a sufficient drop
        j = i + 1
        bottom_price = peak_price
        bottom_idx = i
        while j < len(bars):
            duration_s = (bars[j]['ts_ms'] - bars[peak_idx]['ts_ms']) / 1000.0
            if duration_s > max_duration_s:
                break
            if bars[j]['low'] < bottom_price:
                bottom_price = bars[j]['low']
                bottom_idx = j
            j += 1

        drop_pct = (peak_price - bottom_price) / peak_price * 100.0 if peak_price else 0
        if drop_pct >= min_drop_pct:
            # Find recovery point (close back above bottom + drop_pct * 0.5)
            recovery_target = bottom_price * (1 + drop_pct * 0.3 / 100.0)
            recovery_idx = bottom_idx
            k = bottom_idx + 1
            while k < len(bars) and k - bottom_idx < 180:
                if bars[k]['high'] >= recovery_target:
                    recovery_idx = k
                    break
                k += 1
            crashes.append({
                'peak_idx': peak_idx,
                'peak_price': peak_price,
                'peak_ts': bars[peak_idx]['ts_ms'],
                'bottom_idx': bottom_idx,
                'bottom_price': bottom_price,
                'bottom_ts': bars[bottom_idx]['ts_ms'],
                'recovery_idx': recovery_idx,
                'drop_pct': drop_pct,
                'duration_s': (bars[bottom_idx]['ts_ms'] - bars[peak_idx]['ts_ms']) / 1000.0,
            })
            i = recovery_idx + 1
        else:
            i += 1
    return crashes


def features_at_bottom(bars, bottom_idx):
    """Compute 1s features at the bottom tick.

    These mirror the entry_meta features the bot would have at decision time.
    """
    if bottom_idx < 5:
        return {}
    win_60 = [b for b in bars[max(0, bottom_idx - 60):bottom_idx + 1]]
    win_120 = [b for b in bars[max(0, bottom_idx - 120):bottom_idx + 1]]
    if not win_60:
        return {}
    bottom = bars[bottom_idx]
    # red_count_60s
    red_60 = sum(1 for b in win_60 if b['close'] < b['open'])
    # range_pct_60s (high-low / mid)
    hi_60 = max(b['high'] for b in win_60)
    lo_60 = min(b['low'] for b in win_60)
    mid_60 = (hi_60 + lo_60) / 2 if (hi_60 + lo_60) > 0 else 1
    range_pct = (hi_60 - lo_60) / mid_60 * 100.0 if mid_60 else 0
    # close_pos_60s — where is current close inside 60s range (0=low, 1=high)
    close_pos = (bottom['close'] - lo_60) / (hi_60 - lo_60) if hi_60 > lo_60 else 0.5
    # cum_3min_pct
    if win_120 and win_120[0]['close']:
        cum_120s_pct = (bottom['close'] - win_120[0]['close']) / win_120[0]['close'] * 100.0
    else:
        cum_120s_pct = 0
    # vol surge — last 5 bars vs prior 30
    vol_last5 = sum(b['volume_usd'] for b in bars[max(0, bottom_idx - 4):bottom_idx + 1])
    vol_prior30 = sum(b['volume_usd'] for b in bars[max(0, bottom_idx - 35):bottom_idx - 4])
    vol_ratio = vol_last5 / (vol_prior30 / 6.0) if vol_prior30 > 0 else 0
    # cascade length (consecutive reds ending at bottom_idx)
    cascade_len = 0
    for b in reversed(win_60):
        if b['close'] < b['open']:
            cascade_len += 1
        else:
            break
    # lower wick ratio
    body = abs(bottom['close'] - bottom['open'])
    lower_wick = min(bottom['open'], bottom['close']) - bottom['low']
    upper_wick = bottom['high'] - max(bottom['open'], bottom['close'])
    total_range = bottom['high'] - bottom['low']
    lwr = lower_wick / total_range if total_range > 0 else 0
    return {
        'red_60s': red_60,
        'range_pct_60s': range_pct,
        'close_pos_60s': close_pos,
        'cum_120s_pct': cum_120s_pct,
        'vol_burst_5_30': vol_ratio,
        'cascade_len': cascade_len,
        'lower_wick_ratio': lwr,
    }


def main():
    print("Fetching BURNIE 1s history...")
    bars = fetch_all_1s()
    print(f"Got {len(bars)} 1s bars covering "
          f"{(bars[-1]['ts_ms'] - bars[0]['ts_ms']) / 1000.0 / 60.0:.1f} min")
    print(f"From {datetime.fromtimestamp(bars[0]['ts_ms']/1000, tz=timezone.utc).strftime('%H:%M:%S UTC')} "
          f"to {datetime.fromtimestamp(bars[-1]['ts_ms']/1000, tz=timezone.utc).strftime('%H:%M:%S UTC')}")
    print()

    # Find crashes >= 6% drop within 2 min
    crashes = find_crashes(bars, min_drop_pct=6.0, max_duration_s=180)
    print(f"Found {len(crashes)} crashes (>=6% drop within 3 min):")
    print()
    print(f"{'#':>3} {'peak_time':12s} {'bottom_time':12s} {'dur_s':>6s} {'drop':>6s} "
          f"{'red60':>5s} {'rng%':>5s} {'cpos':>5s} {'cum120':>7s} {'vbst':>5s} "
          f"{'cascln':>6s} {'lwr':>5s}")
    print("-" * 110)
    for i, c in enumerate(crashes, 1):
        feats = features_at_bottom(bars, c['bottom_idx'])
        pk_t = datetime.fromtimestamp(c['peak_ts']/1000, tz=timezone.utc)
        bo_t = datetime.fromtimestamp(c['bottom_ts']/1000, tz=timezone.utc)
        from zoneinfo import ZoneInfo
        pk_ct = pk_t.astimezone(ZoneInfo('America/Chicago'))
        bo_ct = bo_t.astimezone(ZoneInfo('America/Chicago'))
        print(f"{i:>3} {pk_ct.strftime('%H:%M:%S'):12s} {bo_ct.strftime('%H:%M:%S'):12s} "
              f"{c['duration_s']:>5.0f}s {c['drop_pct']:>5.1f}% "
              f"{feats.get('red_60s', 0):>5d} "
              f"{feats.get('range_pct_60s', 0):>4.1f}% "
              f"{feats.get('close_pos_60s', 0):>4.2f} "
              f"{feats.get('cum_120s_pct', 0):>+6.1f}% "
              f"{feats.get('vol_burst_5_30', 0):>4.1f}x "
              f"{feats.get('cascade_len', 0):>5d} "
              f"{feats.get('lower_wick_ratio', 0):>4.2f}")

    print()
    print("Bottoms shown in CT (Central Time). User-flagged crashes were at "
          "4:10-4:40, 5:28-7:30, 7:50-9:00 on the chart timestamp.")

    # Save data for further analysis
    import json
    Path('.burnie_analysis.json').write_text(json.dumps({
        'bar_count': len(bars),
        'crashes': [
            {**{k: v for k, v in c.items() if k != 'features'},
             'features': features_at_bottom(bars, c['bottom_idx'])}
            for c in crashes
        ],
    }, default=float, indent=2))
    print()
    print("Saved to .burnie_analysis.json")


if __name__ == '__main__':
    main()
