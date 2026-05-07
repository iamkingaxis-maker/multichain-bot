"""Fast-mover pattern miner — find entry features that predict
RAPID +8%+ moves (within 20 min) vs slow drifters.

User feedback: existing dip_buy entries are slow. WCOR/ZEREBRO/ORCA
all sit flat at entry. Need triggers that catch tokens that ACTUALLY
move fast.

Target outcome: entry where the next 20 minutes show:
  - Peak >= +8% (TP1 threshold)
  - No drop to -12% before peak (no early stop)
Categorize:
  FAST_WIN:   max 20-min gain >= +8% AND no -5% drop first
  SLOW_WIN:   max 60-min gain >= +8% but >20 min to reach
  FLAT:       max 60-min gain in [-5%, +5%]
  LOSER:      hits -8% within 60 min

For each category, compute median/p75 of candidate entry features:
  - Recent volume surge (vol_now / avg_prior_10)
  - 5m vol surge
  - Body size (current candle)
  - Body sizes of last 3-5 (acceleration)
  - Recent consec_green count
  - 1m higher-highs in last 5
  - Volume at entry candle vs 30-bar avg

Surface features where FAST_WIN distribution is distinctive.
"""
import json
from collections import defaultdict


def aggregate_5m(bars_1m):
    if not bars_1m:
        return []
    out = []
    grp = []
    grp_anchor = None
    for b in bars_1m:
        ts = int(b['ts'])
        anchor = ts - (ts % 300)
        if grp_anchor is None or anchor != grp_anchor:
            if grp:
                out.append({
                    'ts': grp_anchor,
                    'o': grp[0]['o'], 'c': grp[-1]['c'],
                    'h': max(x['h'] for x in grp),
                    'l': min(x['l'] for x in grp),
                    'v': sum(x.get('v') or 0 for x in grp),
                })
            grp = [b]
            grp_anchor = anchor
        else:
            grp.append(b)
    if grp:
        out.append({
            'ts': grp_anchor,
            'o': grp[0]['o'], 'c': grp[-1]['c'],
            'h': max(x['h'] for x in grp),
            'l': min(x['l'] for x in grp),
            'v': sum(x.get('v') or 0 for x in grp),
        })
    return out


def categorize_outcome(bars_1m, i):
    """Return outcome category at bar i: FAST_WIN / SLOW_WIN / FLAT / LOSER."""
    entry_p = bars_1m[i].get('c', 0)
    if entry_p <= 0:
        return None
    # Forward 60 minutes (60 1m bars)
    fwd = bars_1m[i+1:min(len(bars_1m), i+61)]
    if len(fwd) < 30:
        return None
    # Track first 20 min
    fwd_20m = fwd[:20]
    fwd_60m = fwd
    # Compute path
    max_20m_gain = max((b['h'] / entry_p - 1) * 100 for b in fwd_20m)
    min_20m_drop = min((b['l'] / entry_p - 1) * 100 for b in fwd_20m)
    max_60m_gain = max((b['h'] / entry_p - 1) * 100 for b in fwd_60m)
    min_60m_drop = min((b['l'] / entry_p - 1) * 100 for b in fwd_60m)

    if max_20m_gain >= 8 and min_20m_drop > -5:
        return 'FAST_WIN'
    if max_60m_gain >= 8 and min_60m_drop > -8:
        return 'SLOW_WIN'
    if min_60m_drop <= -8:
        return 'LOSER'
    return 'FLAT'


def extract_features(bars_1m, i):
    """Extract candidate trigger features at bar i."""
    if i < 30 or i >= len(bars_1m) - 1:
        return None
    cur = bars_1m[i]
    if cur.get('o', 0) <= 0 or cur.get('c', 0) <= 0:
        return None
    # Body / candle features
    body_pct = (cur['c'] - cur['o']) / cur['o'] * 100
    range_pct = (cur['h'] - cur['l']) / cur['o'] * 100 if cur['o'] > 0 else 0
    upper_wick = (cur['h'] - max(cur['o'], cur['c'])) / cur['o'] * 100 if cur['o'] > 0 else 0
    lower_wick = (min(cur['o'], cur['c']) - cur['l']) / cur['o'] * 100 if cur['o'] > 0 else 0
    is_green = cur['c'] > cur['o']

    # Recent 1m features
    last5 = bars_1m[i-4:i+1]
    last10 = bars_1m[i-9:i+1]
    last30 = bars_1m[i-29:i+1]
    consec_green = 0
    for b in reversed(last5):
        if b['c'] > b['o']:
            consec_green += 1
        else:
            break
    consec_red_before_now = 0
    for b in reversed(bars_1m[i-5:i]):
        if b['c'] < b['o']:
            consec_red_before_now += 1
        else:
            break
    red5_count = sum(1 for b in last5[:-1] if b['c'] < b['o'])
    green5_count = sum(1 for b in last5 if b['c'] > b['o'])
    higher_highs_5 = sum(1 for j in range(1, 5)
                        if last5[j]['h'] > last5[j-1]['h'])

    # Volume features
    vol_cur = cur.get('v', 0)
    avg_vol_prior_10 = sum(b.get('v', 0) for b in bars_1m[i-10:i]) / 10
    avg_vol_prior_30 = sum(b.get('v', 0) for b in bars_1m[i-30:i]) / 30
    vol_spike_10 = vol_cur / avg_vol_prior_10 if avg_vol_prior_10 > 0 else 0
    vol_spike_30 = vol_cur / avg_vol_prior_30 if avg_vol_prior_30 > 0 else 0

    # Body acceleration (last 3 bodies vs prior 3 bodies)
    last3_bodies = [(b['c'] - b['o']) / b['o'] * 100 for b in bars_1m[i-2:i+1] if b['o'] > 0]
    prior3_bodies = [(b['c'] - b['o']) / b['o'] * 100 for b in bars_1m[i-5:i-2] if b['o'] > 0]
    body_accel = (
        (sum(abs(x) for x in last3_bodies) - sum(abs(x) for x in prior3_bodies))
        if (last3_bodies and prior3_bodies) else None
    )

    # Cumulative recent move
    cum_3min = ((cur['c'] / bars_1m[i-3]['c'] - 1) * 100) if bars_1m[i-3]['c'] > 0 else 0
    cum_5min = ((cur['c'] / bars_1m[i-5]['c'] - 1) * 100) if bars_1m[i-5]['c'] > 0 else 0
    cum_10min = ((cur['c'] / bars_1m[i-10]['c'] - 1) * 100) if bars_1m[i-10]['c'] > 0 else 0
    cum_30min = ((cur['c'] / bars_1m[i-30]['c'] - 1) * 100) if bars_1m[i-30]['c'] > 0 else 0

    # 5m features (aggregated)
    cs5 = aggregate_5m(bars_1m[max(0, i-60):i+1])
    if len(cs5) >= 3:
        cs5_last = cs5[-1]
        cs5_body_pct = ((cs5_last['c'] - cs5_last['o']) / cs5_last['o'] * 100
                        if cs5_last['o'] > 0 else 0)
        cs5_consec_green = 0
        for b in reversed(cs5[-3:]):
            if b['c'] > b['o']:
                cs5_consec_green += 1
            else:
                break
    else:
        cs5_body_pct = 0
        cs5_consec_green = 0

    return {
        'is_green': is_green,
        'body_pct': body_pct,
        'range_pct': range_pct,
        'upper_wick_pct': upper_wick,
        'lower_wick_pct': lower_wick,
        'consec_green_now': consec_green,
        'consec_red_before': consec_red_before_now,
        'red5_count_excl_now': red5_count,
        'green5_count': green5_count,
        'higher_highs_5': higher_highs_5,
        'vol_spike_10': vol_spike_10,
        'vol_spike_30': vol_spike_30,
        'body_accel': body_accel,
        'cum_3min_pct': cum_3min,
        'cum_5min_pct': cum_5min,
        'cum_10min_pct': cum_10min,
        'cum_30min_pct': cum_30min,
        'cs5_body_pct': cs5_body_pct,
        'cs5_consec_green': cs5_consec_green,
    }


def main():
    data = json.load(open('.deep_token_bars_master.json', encoding='utf-8'))
    tokens = data.get('tokens', {})
    print(f"Tokens in master: {len(tokens)}")

    cohorts = defaultdict(list)
    for addr, info in tokens.items():
        bars = info.get('bars') or []
        if len(bars) < 100:
            continue
        for i in range(30, len(bars) - 61):
            outcome = categorize_outcome(bars, i)
            if outcome is None:
                continue
            features = extract_features(bars, i)
            if features is None:
                continue
            cohorts[outcome].append(features)

    print(f"\nCohort sizes:")
    for c in ('FAST_WIN', 'SLOW_WIN', 'FLAT', 'LOSER'):
        print(f"  {c}: n={len(cohorts.get(c, []))}")
    print()

    # Print median + p75 of each feature per cohort
    feats = list(cohorts['FAST_WIN'][0].keys()) if cohorts.get('FAST_WIN') else []
    print(f"{'feature':<25} {'FAST_WIN_med':>13} {'SLOW_WIN_med':>13} {'FLAT_med':>10} {'LOSER_med':>10} {'fast_vs_flat':>13}")
    print("-" * 95)

    def med(arr, key):
        vals = []
        for f in arr:
            v = f.get(key)
            if v is None: continue
            try: vals.append(float(v))
            except: continue
        if not vals:
            return None
        s = sorted(vals)
        return s[len(s)//2]

    for feat in feats:
        fw_m = med(cohorts.get('FAST_WIN', []), feat)
        sw_m = med(cohorts.get('SLOW_WIN', []), feat)
        fl_m = med(cohorts.get('FLAT', []), feat)
        lo_m = med(cohorts.get('LOSER', []), feat)
        if fw_m is None or fl_m is None:
            continue
        diff = fw_m - fl_m
        def fmt(v):
            if v is None: return f"{'-':>13}"
            return f"{v:>+13.3f}"
        print(f"{feat:<25} {fmt(fw_m)} {fmt(sw_m)} {fmt(fl_m):>10} {fmt(lo_m):>10} {diff:>+12.3f}")

    print()
    print("=== TOP DISCRIMINATING FEATURES (fast-win lift over flat) ===")
    print("Compare percentile thresholds on each feature; highest fast-win share")
    print()

    def discrimination_test(feat):
        """For each quartile, compute fast-win share."""
        all_data = []
        for cat in ('FAST_WIN', 'SLOW_WIN', 'FLAT', 'LOSER'):
            for f in cohorts.get(cat, []):
                v = f.get(feat)
                if v is None: continue
                try: all_data.append((float(v), cat))
                except: continue
        if len(all_data) < 100:
            return None
        all_data.sort(key=lambda x: x[0])
        n = len(all_data)
        q = []
        for i in range(4):
            chunk = all_data[i*n//4:(i+1)*n//4]
            if not chunk: continue
            fw = sum(1 for _, c in chunk if c == 'FAST_WIN')
            q.append({
                'thr': chunk[-1][0],
                'fast_share': fw / len(chunk) * 100,
                'n': len(chunk),
            })
        return q

    print(f"{'feature':<25} {'q1<=':>9} {'q1_fast%':>9} {'q4>=':>9} {'q4_fast%':>9} {'q4-q1':>8}")
    print("-" * 75)
    for feat in feats:
        d = discrimination_test(feat)
        if not d or len(d) < 4:
            continue
        q1, q4 = d[0], d[3]
        diff = q4['fast_share'] - q1['fast_share']
        if abs(diff) < 1.0:
            continue
        print(f"{feat:<25} {q1['thr']:>+9.2f} {q1['fast_share']:>8.1f}% {q4['thr']:>+9.2f} {q4['fast_share']:>8.1f}% {diff:>+7.1f}%")


if __name__ == "__main__":
    main()
