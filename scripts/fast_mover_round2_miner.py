"""Round-2 fast-mover miner: find patterns ORTHOGONAL to the 3 already-shipped.

Already shipped triggers (exclude their fires from search space):
  T1 momentum_continuation: 4+ consec green 1m + vol_spike_30 >= 1.5x
  T2 explosive_break: r>=4x + 5m vol>=4x + 3 consec green 5m + cum3>=1%
  T3 range_expansion_qualified: r>=6x + vol>=2x + cum3>=1% + 3 HH

Look at REMAINING fast/loser bars (not matching T1/T2/T3) and find
NEW feature combinations with WR>=60%.

New candidate patterns to test:
  - Hammer/lower-wick reversal + vol confirmation
  - Multi-bar sequence: G-R-G-G or similar
  - Sustained 1m climb (cum_5min and cum_10min positive)
  - Sharp single-bar body (large body without explosive range)
  - Body acceleration without 4-consec-green requirement
  - Vol velocity (3 bars increasing) + green
  - Specific 5m TF: 1+ green 5m AND 5m green body >= X%
"""
import json
import sys
sys.path.insert(0, '.')
import importlib.util
spec = importlib.util.spec_from_file_location('fmm', 'scripts/fast_mover_miner.py')
fmm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fmm)
from collections import defaultdict


def t1_momentum_continuation(features, bars, i):
    if features['consec_green_now'] < 4:
        return False
    if features['vol_spike_30'] < 1.5:
        return False
    if not features['is_green']:
        return False
    return True


def t2_explosive_break(features, bars, i):
    """Approximate predicate from features."""
    if not features['is_green']:
        return False
    # range expansion >= 4x
    if features['range_pct'] <= 0:
        return False
    # Use range_pct directly — proxy for expansion
    # Need 5m vol >= 4x — use cs5_vol_spike
    # cs5_vol_spike not in standard features, need to use vol_spike_30 as proxy
    if features['vol_spike_30'] < 4.0:  # approximation
        return False
    if features['cs5_consec_green'] < 3:
        return False
    if features['cum_3min_pct'] < 1.0:
        return False
    return True


def t3_range_expansion_qualified(features, bars, i):
    if not features['is_green']:
        return False
    if features['vol_spike_30'] < 2.0:
        return False
    if features['cum_3min_pct'] < 1.0:
        return False
    if features['higher_highs_5'] < 3:
        return False
    return True


def already_in_existing(features, bars, i):
    return (t1_momentum_continuation(features, bars, i)
            or t2_explosive_break(features, bars, i)
            or t3_range_expansion_qualified(features, bars, i))


def main():
    data = json.load(open('.deep_token_bars_master.json', encoding='utf-8'))
    tokens = data.get('tokens', {})
    print(f"Tokens in master: {len(tokens)}")

    # Filter to fast-mover cohort
    fast_tokens = {}
    for addr, info in tokens.items():
        bars = info.get('bars') or []
        if len(bars) >= 100:
            # Inline fast-mover check
            for i in range(len(bars) - 20):
                ep = bars[i]['c']
                if ep <= 0: continue
                mg = max((bars[j]['h']/ep-1)*100 for j in range(i+1, i+21))
                if mg >= 10:
                    fast_tokens[addr] = info
                    break
    print(f"Fast-mover tokens: {len(fast_tokens)}")
    print()

    # Collect fast/loser cohorts excluding existing triggers
    fast_remaining = []
    loser_remaining = []
    for addr, info in fast_tokens.items():
        bars = info.get('bars') or []
        for i in range(30, len(bars) - 61):
            outcome = fmm.categorize_outcome(bars, i)
            if outcome not in ('FAST_WIN', 'LOSER'):
                continue
            features = fmm.extract_features(bars, i)
            if features is None:
                continue
            if already_in_existing(features, bars, i):
                continue
            if outcome == 'FAST_WIN':
                fast_remaining.append((features, bars, i))
            else:
                loser_remaining.append((features, bars, i))
    print(f"Remaining (orthogonal to T1/T2/T3):")
    print(f"  FAST_WIN: {len(fast_remaining)}")
    print(f"  LOSER:    {len(loser_remaining)}")
    print()

    # ── Test candidate patterns ────────────────────────────────

    candidates = []

    # 1. Hammer reversal: lower_wick > 2x body + green close + vol_spike
    def C1(features, bars, i):
        cur = bars[i]
        if cur['o'] <= 0: return False
        if cur['c'] <= cur['o']: return False  # green
        body = abs(cur['c'] - cur['o'])
        lower_wick = min(cur['o'], cur['c']) - cur['l']
        if body <= 0: return False
        if lower_wick / body < 2.0: return False
        if features['vol_spike_30'] < 1.5: return False
        return True
    candidates.append(("hammer_vol", C1))

    def C1b(features, bars, i):
        cur = bars[i]
        if cur['o'] <= 0: return False
        if cur['c'] <= cur['o']: return False
        body = abs(cur['c'] - cur['o'])
        lower_wick = min(cur['o'], cur['c']) - cur['l']
        if body <= 0: return False
        if lower_wick / body < 3.0: return False
        if features['vol_spike_30'] < 2.0: return False
        return True
    candidates.append(("hammer_strict", C1b))

    # 2. Sustained climb: cum_5min >= 2% AND cum_10min >= 3% (multi-window momentum)
    def C2a(features, bars, i):
        if features['cum_5min_pct'] < 2.0: return False
        if features['cum_10min_pct'] < 3.0: return False
        if not features['is_green']: return False
        return True
    candidates.append(("sustained_climb_2_3", C2a))

    def C2b(features, bars, i):
        if features['cum_5min_pct'] < 3.0: return False
        if features['cum_10min_pct'] < 5.0: return False
        if not features['is_green']: return False
        return True
    candidates.append(("sustained_climb_3_5", C2b))

    # 3. Big body single-bar: body >= 3% green + vol_spike
    def C3a(features, bars, i):
        cur = bars[i]
        if cur['o'] <= 0: return False
        if cur['c'] <= cur['o']: return False
        body_pct = (cur['c'] - cur['o']) / cur['o'] * 100
        if body_pct < 3.0: return False
        if features['vol_spike_30'] < 1.5: return False
        return True
    candidates.append(("big_body_3pct_vol", C3a))

    def C3b(features, bars, i):
        cur = bars[i]
        if cur['o'] <= 0: return False
        if cur['c'] <= cur['o']: return False
        body_pct = (cur['c'] - cur['o']) / cur['o'] * 100
        if body_pct < 5.0: return False
        if features['vol_spike_30'] < 2.0: return False
        return True
    candidates.append(("big_body_5pct_vol", C3b))

    # 4. Vol velocity: 3 bars vol monotonically increasing AND green
    def C4(features, bars, i):
        if i < 3: return False
        v0, v1, v2 = bars[i-2].get('v',0), bars[i-1].get('v',0), bars[i].get('v',0)
        if not (v2 > v1 > v0): return False
        if not features['is_green']: return False
        if features['vol_spike_30'] < 1.5: return False
        return True
    candidates.append(("vol_accel_green", C4))

    # 5. Body acceleration: last 3 bodies avg > prior 3 bodies avg * 2 + green
    def C5(features, bars, i):
        if i < 6: return False
        last3 = [(bars[j]['c']-bars[j]['o'])/bars[j]['o']*100
                 for j in range(i-2, i+1) if bars[j]['o']>0]
        prior3 = [(bars[j]['c']-bars[j]['o'])/bars[j]['o']*100
                  for j in range(i-5, i-2) if bars[j]['o']>0]
        if len(last3) != 3 or len(prior3) != 3: return False
        avg_l = sum(last3)/3
        avg_p = sum(prior3)/3
        if avg_l < 1.0: return False  # at least 1% avg green body
        if avg_p != 0 and avg_l / abs(avg_p) < 2.0: return False
        if not features['is_green']: return False
        return True
    candidates.append(("body_accel_2x", C5))

    # 6. 5m alignment: cs5_consec_green >= 2 + 1m green + cum3 > 0
    def C6a(features, bars, i):
        if features['cs5_consec_green'] < 2: return False
        if not features['is_green']: return False
        if features['cum_3min_pct'] < 0: return False
        return True
    candidates.append(("5m_2green_align", C6a))

    def C6b(features, bars, i):
        if features['cs5_consec_green'] < 3: return False
        if not features['is_green']: return False
        return True
    candidates.append(("5m_3green_align", C6b))

    # 7. Higher-highs sequence: hh in last 10 >= 6 + green + vol
    def C7(features, bars, i):
        if i < 10: return False
        last10 = bars[i-9:i+1]
        hh10 = sum(1 for j in range(1, 10) if last10[j]['h'] > last10[j-1]['h'])
        if hh10 < 6: return False
        if not features['is_green']: return False
        if features['vol_spike_30'] < 1.2: return False
        return True
    candidates.append(("hh10_6_green_vol", C7))

    # 8. Range with green vol but lower thresholds (more fires)
    def _range_exp(bars, i):
        if i < 5: return 0
        cur = bars[i]
        if cur['o'] <= 0: return 0
        cur_r = (cur['h']-cur['l'])/cur['o']*100
        last5 = [(bars[j]['h']-bars[j]['l'])/bars[j]['o']*100
                 for j in range(i-5, i) if bars[j]['o']>0]
        if not last5: return 0
        avg5 = sum(last5)/len(last5)
        if avg5 <= 0: return 0
        return cur_r / avg5

    def C8a(features, bars, i):
        if _range_exp(bars, i) < 2.0: return False
        if not features['is_green']: return False
        if features['vol_spike_30'] < 1.5: return False
        if features['cum_3min_pct'] < 0.5: return False
        return True
    candidates.append(("range_2x_vol_cum05", C8a))

    def C8b(features, bars, i):
        if _range_exp(bars, i) < 3.0: return False
        if not features['is_green']: return False
        if features['vol_spike_30'] < 1.5: return False
        if features['cum_3min_pct'] < 0.5: return False
        return True
    candidates.append(("range_3x_vol_cum05", C8b))

    # 9. Combined conservative: 3 consec green + body >= 2% + vol >= 2x
    def C9(features, bars, i):
        if features['consec_green_now'] < 3: return False
        cur = bars[i]
        if cur['o'] <= 0: return False
        body_pct = (cur['c'] - cur['o']) / cur['o'] * 100
        if body_pct < 2.0: return False
        if features['vol_spike_30'] < 2.0: return False
        return True
    candidates.append(("3green_body2pct_vol2x", C9))

    # 10. 5m strong body + 1m green confirm
    def C10(features, bars, i):
        if features['cs5_body_pct'] < 4.0: return False
        if not features['is_green']: return False
        if features['vol_spike_30'] < 1.2: return False
        return True
    candidates.append(("5m_body4pct_green", C10))

    # ── Evaluate each candidate ──────────────────────────────
    print(f"=== Candidate patterns (orthogonal to T1/T2/T3) ===")
    print(f"{'name':<28} {'fast':>5} {'loser':>5} {'total':>6} {'fast%':>6} {'lift':>6}")
    print("-" * 70)

    base_ratio = len(fast_remaining) / max(1, len(loser_remaining))
    results = []
    for name, pred in candidates:
        f_match = sum(1 for f, b, i in fast_remaining if pred(f, b, i))
        l_match = sum(1 for f, b, i in loser_remaining if pred(f, b, i))
        total = f_match + l_match
        if total < 30:
            continue
        ratio = f_match / max(1, l_match)
        lift = ratio / base_ratio
        fast_share = f_match / total * 100
        results.append({
            'name': name, 'f_match': f_match, 'l_match': l_match,
            'total': total, 'fast_share': fast_share, 'lift': lift,
        })
    results.sort(key=lambda r: -r['fast_share'])
    for r in results:
        print(f"{r['name']:<28} {r['f_match']:>5} {r['l_match']:>5} {r['total']:>6} "
              f"{r['fast_share']:>5.1f}% {r['lift']:>5.2f}x")


if __name__ == "__main__":
    main()
