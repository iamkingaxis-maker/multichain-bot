"""Fast-mover vs LOSER discrimination — find features at entry-time that
predict fast +8% bounce in 20 min vs -8% drop in 60 min.

The key question: what distinguishes a token about to FAST_WIN from one
about to LOSE? Both can have similar surface volatility/volume.

Method:
  1. Reuse fast_mover_miner.py categorization
  2. For each feature, compute P(feature_x_high | FAST_WIN) vs
     P(feature_x_high | LOSER). High lift = discriminating feature.
  3. Find combinations of 2-3 features that strongly enrich FAST_WIN
"""
import json
from collections import defaultdict
from itertools import combinations
import sys, importlib.util
spec = importlib.util.spec_from_file_location('fmm', 'scripts/fast_mover_miner.py')
fmm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fmm)


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
            outcome = fmm.categorize_outcome(bars, i)
            if outcome is None:
                continue
            features = fmm.extract_features(bars, i)
            if features is None:
                continue
            cohorts[outcome].append(features)

    print(f"\nCohort sizes:")
    for c in ('FAST_WIN', 'SLOW_WIN', 'FLAT', 'LOSER'):
        print(f"  {c}: n={len(cohorts.get(c, []))}")

    fast = cohorts['FAST_WIN']
    loss = cohorts['LOSER']

    # For each feature, find threshold where P(FAST_WIN | feat>thresh) is max
    feats = list(fast[0].keys()) if fast else []

    print(f"\n{'='*80}")
    print("Single-feature discrimination (find threshold maximizing FAST/LOSER ratio)")
    print(f"{'='*80}\n")

    rows = []
    for feat in feats:
        # Combine FAST and LOSER values
        combined = []
        for f in fast:
            v = f.get(feat)
            if v is None: continue
            try: combined.append((float(v), 'F'))
            except: continue
        for f in loss:
            v = f.get(feat)
            if v is None: continue
            try: combined.append((float(v), 'L'))
            except: continue
        if len(combined) < 1000:
            continue
        combined.sort()
        n = len(combined)
        # Try thresholds at 50%, 75%, 90% percentiles
        for pct in (50, 70, 80, 90, 95):
            idx = int(n * pct / 100)
            thr = combined[idx][0]
            above = combined[idx:]
            if len(above) < 100:
                continue
            f_above = sum(1 for _, c in above if c == 'F')
            l_above = sum(1 for _, c in above if c == 'L')
            if l_above == 0:
                continue
            ratio = f_above / l_above
            base_ratio = len(fast) / len(loss)
            lift = ratio / base_ratio
            rows.append({
                'feat': feat,
                'pct': pct,
                'thr': thr,
                'n_above': len(above),
                'f_above': f_above,
                'l_above': l_above,
                'ratio': ratio,
                'lift': lift,
                'fast_share': f_above / len(above) * 100,
            })

    # Sort by lift (descending), filter for meaningful sample
    rows = [r for r in rows if r['n_above'] >= 200]
    rows.sort(key=lambda r: -r['lift'])
    print(f"{'feature':<25} {'pct':>4} {'thresh':>10} {'n_above':>8} {'fast/loser':>11} {'fast%':>7} {'lift':>6}")
    for r in rows[:30]:
        print(f"{r['feat']:<25} {r['pct']:>3}% {r['thr']:>+9.3f} {r['n_above']:>8} "
              f"{r['f_above']:>5}/{r['l_above']:<4} {r['fast_share']:>6.1f}% {r['lift']:>5.2f}x")

    # Two-feature combinations on top features
    print(f"\n{'='*80}")
    print("Two-feature AND combinations (top single discriminators)")
    print(f"{'='*80}\n")

    top_feats = list(set([r['feat'] for r in rows[:8]]))[:6]
    print(f"Combining: {top_feats}\n")

    # For each pair, find best combo
    pair_results = []
    for f1, f2 in combinations(top_feats, 2):
        # Find best thresholds
        best = None
        for r1 in [r for r in rows if r['feat'] == f1][:3]:
            for r2 in [r for r in rows if r['feat'] == f2][:3]:
                f_match = sum(
                    1 for f in fast
                    if (f.get(f1) is not None and f.get(f2) is not None
                        and float(f.get(f1)) >= r1['thr']
                        and float(f.get(f2)) >= r2['thr'])
                )
                l_match = sum(
                    1 for f in loss
                    if (f.get(f1) is not None and f.get(f2) is not None
                        and float(f.get(f1)) >= r1['thr']
                        and float(f.get(f2)) >= r2['thr'])
                )
                if l_match == 0 or f_match + l_match < 100:
                    continue
                ratio = f_match / l_match
                fast_share = f_match / (f_match + l_match) * 100
                if best is None or ratio > best['ratio']:
                    best = {
                        'f1': f1, 'thr1': r1['thr'], 'pct1': r1['pct'],
                        'f2': f2, 'thr2': r2['thr'], 'pct2': r2['pct'],
                        'f_match': f_match, 'l_match': l_match,
                        'ratio': ratio, 'fast_share': fast_share,
                    }
        if best:
            pair_results.append(best)
    pair_results.sort(key=lambda r: -r['ratio'])
    for r in pair_results[:15]:
        print(f"{r['f1']}>={r['thr1']:+.2f} AND {r['f2']}>={r['thr2']:+.2f}: "
              f"f={r['f_match']}/l={r['l_match']} "
              f"ratio={r['ratio']:.2f} fast_share={r['fast_share']:.1f}%")


if __name__ == "__main__":
    main()
