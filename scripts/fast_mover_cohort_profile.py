"""Profile fast-mover cohort vs bot's discovery+filter thresholds.

Question: can the bot actually FIND the tokens we're testing on?
Verify mcap, vol_h1, age, etc. fall within the bot's filter window.

Bot's current dip_buy gates (from utils/config.py):
  dip_min_mcap:  $250,000   (DIP_MIN_MCAP env override)
  dip_max_mcap:  $100,000,000
  dip_min_age_days: 0.0  (no age floor)
  Other filters: low_turnover, vol_h1_decay, etc.

If many fast-mover tokens fall outside these windows, we need to widen
the gates OR ship the triggers knowing they'll have less impact than
expected.
"""
import json
from collections import defaultdict


def is_fast_mover_token(bars, fast_pct=10.0, fast_window=20, min_events=1):
    events = 0
    for i in range(len(bars) - fast_window):
        entry_p = bars[i]['c']
        if entry_p <= 0: continue
        max_gain = max((bars[j]['h']/entry_p-1)*100 for j in range(i+1, i+1+fast_window))
        if max_gain >= fast_pct:
            events += 1
            if events >= min_events: return True
    return False


def main():
    data = json.load(open('.deep_token_bars_master.json', encoding='utf-8'))
    tokens = data.get('tokens', {})
    print(f"Tokens in master: {len(tokens)}")

    # Filter to fast-mover cohort
    fast_tokens = []
    for addr, info in tokens.items():
        bars = info.get('bars') or []
        if len(bars) < 100:
            continue
        if is_fast_mover_token(bars):
            fast_tokens.append(info)
    print(f"Fast-mover cohort: {len(fast_tokens)}")
    print()

    # Profile each fast-mover token
    print("=== MCAP DISTRIBUTION ===")
    mcaps = [info.get('mc') for info in fast_tokens if info.get('mc') is not None]
    print(f"Tokens with mcap recorded: {len(mcaps)}/{len(fast_tokens)}")
    if mcaps:
        s = sorted(mcaps)
        n = len(s)
        print(f"  min:    ${s[0]:>15,.0f}")
        print(f"  p10:    ${s[n//10]:>15,.0f}")
        print(f"  p25:    ${s[n//4]:>15,.0f}")
        print(f"  median: ${s[n//2]:>15,.0f}")
        print(f"  p75:    ${s[3*n//4]:>15,.0f}")
        print(f"  p90:    ${s[9*n//10]:>15,.0f}")
        print(f"  max:    ${s[-1]:>15,.0f}")
        print()
        # Count vs bot bands
        below_min = sum(1 for x in mcaps if x < 250_000)
        in_range = sum(1 for x in mcaps if 250_000 <= x <= 100_000_000)
        above_max = sum(1 for x in mcaps if x > 100_000_000)
        print(f"  Bot filter (250k - 100M):")
        print(f"    BELOW 250k:    {below_min} ({below_min/len(mcaps)*100:.1f}%)")
        print(f"    IN RANGE:      {in_range} ({in_range/len(mcaps)*100:.1f}%)")
        print(f"    ABOVE 100M:    {above_max} ({above_max/len(mcaps)*100:.1f}%)")

    # Volume profile (compute hourly vol from bars)
    print()
    print("=== HOURLY VOLUME (median across all bars) ===")
    hourly_vols = []
    for info in fast_tokens:
        bars = info.get('bars') or []
        if len(bars) < 60: continue
        # Average over rolling 60-bar windows
        for i in range(60, len(bars), 60):
            vol_h1 = sum(b.get('v', 0) for b in bars[i-60:i])
            if vol_h1 > 0:
                hourly_vols.append(vol_h1)
    if hourly_vols:
        s = sorted(hourly_vols)
        n = len(s)
        print(f"  median 1h vol: ${s[n//2]:>10,.0f}")
        print(f"  p25:           ${s[n//4]:>10,.0f}")
        print(f"  p75:           ${s[3*n//4]:>10,.0f}")
        # Common bot threshold checks
        # Note: low_turnover threshold is tied to liq, but a vol_h1 floor exists
        below_10k = sum(1 for x in hourly_vols if x < 10_000)
        below_50k = sum(1 for x in hourly_vols if x < 50_000)
        print(f"  vol_h1 < $10k:  {below_10k/n*100:.1f}%")
        print(f"  vol_h1 < $50k:  {below_50k/n*100:.1f}%")

    # Source distribution (where these tokens come from)
    print()
    print("=== SOURCE DISTRIBUTION ===")
    src_counts = defaultdict(int)
    for info in fast_tokens:
        src_counts[info.get('src') or 'unknown'] += 1
    for src, n in sorted(src_counts.items(), key=lambda x: -x[1]):
        print(f"  {src}: {n}")

    # Token age proxy (length of bar history)
    print()
    print("=== BAR HISTORY DEPTH (proxy for age tracked) ===")
    bar_counts = sorted([len(info.get('bars') or []) for info in fast_tokens])
    if bar_counts:
        n = len(bar_counts)
        print(f"  median bars: {bar_counts[n//2]}")
        print(f"  p25:         {bar_counts[n//4]}")
        print(f"  p75:         {bar_counts[3*n//4]}")
        print(f"  max:         {bar_counts[-1]}")

    # Top fast-mover tokens by mcap (does the bot already see these?)
    print()
    print("=== TOP 10 FAST-MOVERS BY MCAP (in bot range) ===")
    in_range_tokens = [info for info in fast_tokens
                       if info.get('mc') and 250_000 <= info['mc'] <= 100_000_000]
    in_range_tokens.sort(key=lambda x: -x['mc'])
    for info in in_range_tokens[:10]:
        sym = info.get('sym') or 'unknown'
        addr = info.get('addr', '')[:20]
        mc = info['mc']
        bars = info.get('bars') or []
        avg_vol = sum(b.get('v',0) for b in bars[-60:]) if len(bars)>=60 else 0
        print(f"  {sym:<10} {addr:<22} mc=${mc:>12,.0f} recent_vol_h1=${avg_vol:>10,.0f}")

    # Compare bot's DIP_MIN_MCAP setting
    print()
    print("=== BOT FILTER CHECK ===")
    print("Current dip_min_mcap default: $250,000")
    print("Current dip_max_mcap default: $100,000,000")
    print()
    if mcaps:
        excluded_low = sum(1 for x in mcaps if x < 250_000) / len(mcaps) * 100
        excluded_high = sum(1 for x in mcaps if x > 100_000_000) / len(mcaps) * 100
        if excluded_low > 5:
            print(f"  WARNING: {excluded_low:.1f}% of fast-movers below 250k mcap floor")
        if excluded_high > 5:
            print(f"  WARNING: {excluded_high:.1f}% of fast-movers above 100M mcap ceiling")
        if excluded_low <= 5 and excluded_high <= 5:
            print(f"  [OK] >=95% of fast-movers within bot's mcap window")


if __name__ == "__main__":
    main()
