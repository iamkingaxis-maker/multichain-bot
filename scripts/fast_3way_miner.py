"""Fast-mover 3-feature combination miner — orthogonal to momentum_continuation.

We already shipped trigger_momentum_continuation: 4+ consec green + vol_spike_30>=1.5
Now find OTHER patterns that:
  1. Have high FAST_WIN concentration vs LOSER
  2. Don't overlap with momentum_continuation (different mechanism)

Expanded feature set:
  - body_sequences: avg of last 3 bodies, max body, body_ratio (last/prior_3_avg)
  - range_expansion: cur_range vs avg_range_5/10
  - vol_velocity: vol slope across last 3 bars
  - 5m TF: cs5_consec_green, cs5_body, cs5_vol_spike
  - Wick patterns: lower_wick_dom, upper_wick_dom
  - Higher-highs: hh_5, hh_10
  - Cumulative micro-moves: cum_5min, cum_10min
"""
import json
from collections import defaultdict
from itertools import combinations


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
    entry_p = bars_1m[i].get('c', 0)
    if entry_p <= 0:
        return None
    fwd = bars_1m[i+1:min(len(bars_1m), i+61)]
    if len(fwd) < 30:
        return None
    fwd_20m = fwd[:20]
    max_20m_gain = max((b['h'] / entry_p - 1) * 100 for b in fwd_20m)
    min_20m_drop = min((b['l'] / entry_p - 1) * 100 for b in fwd_20m)
    max_60m_gain = max((b['h'] / entry_p - 1) * 100 for b in fwd)
    min_60m_drop = min((b['l'] / entry_p - 1) * 100 for b in fwd)
    if max_20m_gain >= 8 and min_20m_drop > -5:
        return 'FAST_WIN'
    if max_60m_gain >= 8 and min_60m_drop > -8:
        return 'SLOW_WIN'
    if min_60m_drop <= -8:
        return 'LOSER'
    return 'FLAT'


def extract_features(bars_1m, i):
    if i < 30 or i >= len(bars_1m) - 1:
        return None
    cur = bars_1m[i]
    if cur.get('o', 0) <= 0:
        return None

    body_pct = (cur['c'] - cur['o']) / cur['o'] * 100 if cur['o'] > 0 else 0
    range_pct = (cur['h'] - cur['l']) / cur['o'] * 100 if cur['o'] > 0 else 0
    upper_wick = (cur['h'] - max(cur['o'], cur['c'])) / cur['o'] * 100 if cur['o'] > 0 else 0
    lower_wick = (min(cur['o'], cur['c']) - cur['l']) / cur['o'] * 100 if cur['o'] > 0 else 0
    is_green = 1 if cur['c'] > cur['o'] else 0

    last5 = bars_1m[i-4:i+1]
    last10 = bars_1m[i-9:i+1]

    consec_green = 0
    for b in reversed(last5):
        if b['c'] > b['o']:
            consec_green += 1
        else:
            break
    consec_red_before = 0
    for b in reversed(bars_1m[i-5:i]):
        if b['c'] < b['o']:
            consec_red_before += 1
        else:
            break

    # Body sequence stats
    last3_bodies = [(b['c']-b['o'])/b['o']*100 for b in bars_1m[i-2:i+1] if b['o']>0]
    prior3_bodies = [(b['c']-b['o'])/b['o']*100 for b in bars_1m[i-5:i-2] if b['o']>0]
    avg_last3_body = sum(last3_bodies)/len(last3_bodies) if last3_bodies else 0
    avg_prior3_body = sum(prior3_bodies)/len(prior3_bodies) if prior3_bodies else 0
    max_body_5 = max(((b['c']-b['o'])/b['o']*100 for b in last5 if b['o']>0), default=0)
    body_ratio = (avg_last3_body / avg_prior3_body
                  if avg_prior3_body != 0 else 0)

    # Range expansion
    last5_ranges = [(b['h']-b['l'])/b['o']*100 for b in last5 if b['o']>0]
    avg_range_5 = sum(last5_ranges)/len(last5_ranges) if last5_ranges else 0
    range_expansion = range_pct / avg_range_5 if avg_range_5 > 0 else 0

    last10_ranges = [(b['h']-b['l'])/b['o']*100 for b in last10 if b['o']>0]
    avg_range_10 = sum(last10_ranges)/len(last10_ranges) if last10_ranges else 0
    range_expansion_10 = range_pct / avg_range_10 if avg_range_10 > 0 else 0

    # Higher-highs
    hh_5 = sum(1 for j in range(1, 5)
               if last5[j]['h'] > last5[j-1]['h'])
    hh_10 = sum(1 for j in range(1, 10)
                if last10[j]['h'] > last10[j-1]['h'])

    # Volume features
    vol_cur = cur.get('v', 0)
    avg_vol_5 = sum(b.get('v', 0) for b in bars_1m[i-5:i]) / 5
    avg_vol_10 = sum(b.get('v', 0) for b in bars_1m[i-10:i]) / 10
    avg_vol_30 = sum(b.get('v', 0) for b in bars_1m[i-30:i]) / 30
    vol_spike_5 = vol_cur / avg_vol_5 if avg_vol_5 > 0 else 0
    vol_spike_10 = vol_cur / avg_vol_10 if avg_vol_10 > 0 else 0
    vol_spike_30 = vol_cur / avg_vol_30 if avg_vol_30 > 0 else 0

    # Vol velocity (last 3 vol slope)
    last3_vols = [b.get('v', 0) for b in bars_1m[i-2:i+1]]
    vol_increasing = (1 if len(last3_vols) == 3
                      and last3_vols[2] > last3_vols[1] > last3_vols[0]
                      else 0)

    # Cumulative micro-moves
    cum_3min = ((cur['c']/bars_1m[i-3]['c']-1)*100) if bars_1m[i-3]['c']>0 else 0
    cum_5min = ((cur['c']/bars_1m[i-5]['c']-1)*100) if bars_1m[i-5]['c']>0 else 0
    cum_10min = ((cur['c']/bars_1m[i-10]['c']-1)*100) if bars_1m[i-10]['c']>0 else 0

    # 5m features
    cs5 = aggregate_5m(bars_1m[max(0, i-90):i+1])
    if len(cs5) >= 3:
        cs5_last = cs5[-1]
        cs5_body_pct = ((cs5_last['c']-cs5_last['o'])/cs5_last['o']*100
                        if cs5_last['o']>0 else 0)
        cs5_consec_green = 0
        for b in reversed(cs5[-5:] if len(cs5) >= 5 else cs5):
            if b['c'] > b['o']:
                cs5_consec_green += 1
            else:
                break
        cs5_vol_cur = cs5_last.get('v', 0)
        cs5_prior_vols = [b.get('v', 0) for b in cs5[-6:-1] if b.get('v')]
        cs5_avg_vol = (sum(cs5_prior_vols)/len(cs5_prior_vols)
                       if cs5_prior_vols else 0)
        cs5_vol_spike = (cs5_vol_cur / cs5_avg_vol
                         if cs5_avg_vol > 0 else 0)
    else:
        cs5_body_pct = 0
        cs5_consec_green = 0
        cs5_vol_spike = 0

    return {
        'body_pct': body_pct,
        'range_pct': range_pct,
        'upper_wick_pct': upper_wick,
        'lower_wick_pct': lower_wick,
        'is_green': is_green,
        'consec_green_now': consec_green,
        'consec_red_before': consec_red_before,
        'avg_last3_body': avg_last3_body,
        'avg_prior3_body': avg_prior3_body,
        'max_body_5': max_body_5,
        'body_ratio_3v3': body_ratio,
        'range_expansion_5': range_expansion,
        'range_expansion_10': range_expansion_10,
        'hh_5': hh_5,
        'hh_10': hh_10,
        'vol_spike_5': vol_spike_5,
        'vol_spike_10': vol_spike_10,
        'vol_spike_30': vol_spike_30,
        'vol_increasing_3': vol_increasing,
        'cum_3min_pct': cum_3min,
        'cum_5min_pct': cum_5min,
        'cum_10min_pct': cum_10min,
        'cs5_body_pct': cs5_body_pct,
        'cs5_consec_green': cs5_consec_green,
        'cs5_vol_spike': cs5_vol_spike,
    }


# Already-shipped trigger_momentum_continuation predicate
def trigger_momentum_continuation(features, bars, i):
    """Returns True if this bar would have fired our existing trigger."""
    if features['consec_green_now'] < 4:
        return False
    if features['vol_spike_30'] < 1.5:
        return False
    if not features['is_green']:
        return False
    return True


def main():
    data = json.load(open('.deep_token_bars_master.json', encoding='utf-8'))
    tokens = data.get('tokens', {})
    print(f"Tokens in master: {len(tokens)}")

    # Collect all bars + features + outcomes
    all_data = []  # list of (features, outcome, already_in_mc)
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
            already_in_mc = trigger_momentum_continuation(features, bars, i)
            all_data.append((features, outcome, already_in_mc))

    print(f"Total scored bars: {len(all_data)}")
    cohorts = defaultdict(list)
    for f, o, in_mc in all_data:
        cohorts[o].append((f, in_mc))
    for c in ('FAST_WIN', 'SLOW_WIN', 'FLAT', 'LOSER'):
        in_mc_count = sum(1 for f, m in cohorts[c] if m)
        print(f"  {c}: n={len(cohorts[c])} (already in mc: {in_mc_count})")

    # We want to find features where high values are over-represented in
    # FAST_WIN but UNDER-represented in our existing mc trigger.
    # That gives us complementary triggers.

    # Simplification: filter out bars that already match momentum_continuation,
    # then run discrimination on the REMAINING bars.
    fast_remaining = [(f, m) for f, m in cohorts['FAST_WIN'] if not m]
    loser_remaining = [(f, m) for f, m in cohorts['LOSER'] if not m]
    print(f"\nAfter excluding momentum_continuation matches:")
    print(f"  FAST_WIN remaining: {len(fast_remaining)}")
    print(f"  LOSER remaining:    {len(loser_remaining)}")
    print()

    feats = list(fast_remaining[0][0].keys())

    # Single-feature discrimination on remaining
    print(f"=== SINGLE-FEATURE on remaining (mc-orthogonal) ===")
    print(f"{'feature':<22} {'pct':>4} {'thr':>9} {'n_above':>7} {'fast/loser':>11} {'fast%':>7} {'lift':>6}")
    rows = []
    for feat in feats:
        combined = []
        for f, _ in fast_remaining:
            v = f.get(feat)
            if v is not None:
                try: combined.append((float(v), 'F'))
                except: pass
        for f, _ in loser_remaining:
            v = f.get(feat)
            if v is not None:
                try: combined.append((float(v), 'L'))
                except: pass
        if len(combined) < 1000:
            continue
        combined.sort()
        n = len(combined)
        for pct in (70, 80, 90, 95):
            idx = int(n * pct / 100)
            thr = combined[idx][0]
            above = combined[idx:]
            if len(above) < 200:
                continue
            f_above = sum(1 for _, c in above if c == 'F')
            l_above = sum(1 for _, c in above if c == 'L')
            if l_above < 50:
                continue
            ratio = f_above / l_above
            base_ratio = len(fast_remaining) / max(1, len(loser_remaining))
            lift = ratio / base_ratio
            rows.append({
                'feat': feat, 'pct': pct, 'thr': thr,
                'n_above': len(above), 'f_above': f_above, 'l_above': l_above,
                'ratio': ratio, 'lift': lift,
                'fast_share': f_above/len(above)*100,
            })
    rows.sort(key=lambda r: -r['lift'])
    for r in rows[:25]:
        print(f"{r['feat']:<22} {r['pct']:>3}% {r['thr']:>+9.3f} {r['n_above']:>7} "
              f"{r['f_above']:>4}/{r['l_above']:<4} {r['fast_share']:>6.1f}% {r['lift']:>5.2f}x")

    # 2-feature combos on top features
    print(f"\n=== 2-FEATURE COMBOS (mc-orthogonal) ===")
    top_feats = list(set([r['feat'] for r in rows[:8]]))[:6]
    print(f"Top features for pairing: {top_feats}\n")

    pair_results = []
    for f1, f2 in combinations(top_feats, 2):
        f1_thrs = [r for r in rows if r['feat'] == f1][:3]
        f2_thrs = [r for r in rows if r['feat'] == f2][:3]
        for r1 in f1_thrs:
            for r2 in f2_thrs:
                f_match = sum(
                    1 for f, _ in fast_remaining
                    if (f.get(f1) is not None and f.get(f2) is not None
                        and float(f.get(f1)) >= r1['thr']
                        and float(f.get(f2)) >= r2['thr'])
                )
                l_match = sum(
                    1 for f, _ in loser_remaining
                    if (f.get(f1) is not None and f.get(f2) is not None
                        and float(f.get(f1)) >= r1['thr']
                        and float(f.get(f2)) >= r2['thr'])
                )
                if l_match < 30 or f_match + l_match < 100:
                    continue
                ratio = f_match / l_match
                base_ratio = len(fast_remaining) / max(1, len(loser_remaining))
                lift = ratio / base_ratio
                pair_results.append({
                    'f1': f1, 'thr1': r1['thr'],
                    'f2': f2, 'thr2': r2['thr'],
                    'f_match': f_match, 'l_match': l_match,
                    'ratio': ratio, 'lift': lift,
                    'fast_share': f_match/(f_match+l_match)*100,
                })

    # Dedup near-identical pair results
    pair_results.sort(key=lambda r: -r['lift'])
    seen = set()
    print(f"{'combo':<55} {'n_match':>8} {'f/l':>11} {'fast%':>7} {'lift':>6}")
    for r in pair_results[:30]:
        sig = (r['f1'], r['f2'])
        if sig in seen: continue
        seen.add(sig)
        combo_str = f"{r['f1']}>={r['thr1']:+.2f} AND {r['f2']}>={r['thr2']:+.2f}"
        print(f"{combo_str:<55} {r['f_match']+r['l_match']:>8} "
              f"{r['f_match']:>4}/{r['l_match']:<4} "
              f"{r['fast_share']:>6.1f}% {r['lift']:>5.2f}x")

    # 3-feature combos on top
    print(f"\n=== 3-FEATURE COMBOS (best lift, mc-orthogonal) ===")
    triple_results = []
    for f1, f2, f3 in combinations(top_feats, 3):
        f1_t = [r for r in rows if r['feat'] == f1][:2]
        f2_t = [r for r in rows if r['feat'] == f2][:2]
        f3_t = [r for r in rows if r['feat'] == f3][:2]
        for r1 in f1_t:
            for r2 in f2_t:
                for r3 in f3_t:
                    f_match = sum(
                        1 for f, _ in fast_remaining
                        if (f.get(f1) is not None and f.get(f2) is not None
                            and f.get(f3) is not None
                            and float(f.get(f1)) >= r1['thr']
                            and float(f.get(f2)) >= r2['thr']
                            and float(f.get(f3)) >= r3['thr'])
                    )
                    l_match = sum(
                        1 for f, _ in loser_remaining
                        if (f.get(f1) is not None and f.get(f2) is not None
                            and f.get(f3) is not None
                            and float(f.get(f1)) >= r1['thr']
                            and float(f.get(f2)) >= r2['thr']
                            and float(f.get(f3)) >= r3['thr'])
                    )
                    if l_match < 15 or f_match + l_match < 50:
                        continue
                    ratio = f_match / l_match
                    base_ratio = (len(fast_remaining)
                                  / max(1, len(loser_remaining)))
                    lift = ratio / base_ratio
                    triple_results.append({
                        'f1': f1, 'thr1': r1['thr'],
                        'f2': f2, 'thr2': r2['thr'],
                        'f3': f3, 'thr3': r3['thr'],
                        'f_match': f_match, 'l_match': l_match,
                        'ratio': ratio, 'lift': lift,
                        'fast_share': f_match/(f_match+l_match)*100,
                    })
    triple_results.sort(key=lambda r: -r['lift'])
    seen3 = set()
    for r in triple_results[:20]:
        sig = (r['f1'], r['f2'], r['f3'])
        if sig in seen3: continue
        seen3.add(sig)
        combo = (f"{r['f1']}>={r['thr1']:+.2f}, "
                 f"{r['f2']}>={r['thr2']:+.2f}, "
                 f"{r['f3']}>={r['thr3']:+.2f}")
        print(f"  {combo}: n={r['f_match']+r['l_match']} f/l={r['f_match']}/{r['l_match']} "
              f"fast%={r['fast_share']:.1f}% lift={r['lift']:.2f}x")


if __name__ == "__main__":
    main()
