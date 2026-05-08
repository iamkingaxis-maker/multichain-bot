"""Analyze phantom exit-logic shadow tests:
  - phantom_pnl_pct (baseline)
  - phantom_pnl_pct_smart_bearflip (smart bearflip shadow)
  - phantom_pnl_pct_tp1_100pct (TP1 sells 100%)
"""
import json
import glob
from collections import Counter


files = sorted(glob.glob('.live_forward_test/2026*.json'))
cands = []
for f in files:
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if not d.get('resolved'):
        continue
    for c in d.get('candidates') or []:
        if 'phantom_pnl_usd' not in c:
            continue
        cands.append(c)

print(f'Resolved phantom candidates: {len(cands)}')
print()


def stats(pnls):
    if not pnls:
        return None
    n = len(pnls)
    w = sum(1 for p in pnls if p > 0)
    total_pct = sum(pnls)
    avg_pct = total_pct / n
    wr = w / n * 100
    big_w = sum(1 for p in pnls if p >= 8)
    big_l = sum(1 for p in pnls if p <= -10)
    return dict(n=n, w=w, wr=wr, total_pct=total_pct, avg_pct=avg_pct,
                tp_rate=big_w / n * 100, stop_rate=big_l / n * 100)


# Baseline (current production exit logic)
base = [c['phantom_pnl_pct'] for c in cands if c.get('phantom_pnl_pct') is not None]
sbf = [c['phantom_pnl_pct_smart_bearflip']
       for c in cands if c.get('phantom_pnl_pct_smart_bearflip') is not None]
tp1 = [c['phantom_pnl_pct_tp1_100pct']
       for c in cands if c.get('phantom_pnl_pct_tp1_100pct') is not None]

print('=== Exit logic comparison (across ALL candidates regardless of filter) ===')
print(f'{"variant":<30} {"n":>5} {"WR%":>5} {"avg%":>7} {"TP%":>5} {"Stop%":>6} {"total%":>9}')
for name, pnls in [('baseline (current live)', base),
                    ('smart_bearflip (shadow)', sbf),
                    ('TP1 sells 100% (now live)', tp1)]:
    s = stats(pnls)
    if not s:
        continue
    print(f'  {name:<30} {s["n"]:>5} {s["wr"]:>4.0f}% {s["avg_pct"]:>+5.2f}% '
          f'{s["tp_rate"]:>4.0f}% {s["stop_rate"]:>5.0f}% {s["total_pct"]:>+8.1f}%')

# Restrict to candidates that PASSED the live production stack (S) — the only
# realistic comparison. Other candidates wouldn't have been bought.
print()
print('=== Same comparison, BUT only on candidates the live stack would PASS ===')
live_pass = [c for c in cands if (c.get('verdicts') or {}).get('S_live_prod_stack') == 'PASS']
print(f'  ({len(live_pass)} candidates passed S_live_prod_stack)')
print()
print(f'{"variant":<30} {"n":>5} {"WR%":>5} {"avg%":>7} {"TP%":>5} {"Stop%":>6} {"total%":>9}')
for name, key in [('baseline (current live)', 'phantom_pnl_pct'),
                   ('smart_bearflip', 'phantom_pnl_pct_smart_bearflip'),
                   ('TP1 sells 100%', 'phantom_pnl_pct_tp1_100pct')]:
    pnls = [c[key] for c in live_pass if c.get(key) is not None]
    s = stats(pnls)
    if not s:
        continue
    print(f'  {name:<30} {s["n"]:>5} {s["wr"]:>4.0f}% {s["avg_pct"]:>+5.2f}% '
          f'{s["tp_rate"]:>4.0f}% {s["stop_rate"]:>5.0f}% {s["total_pct"]:>+8.1f}%')

# Restrict further to candidates the BEST gate (P) would PASS
print()
print('=== Same comparison, on candidates P_slip_vel would PASS ===')
p_pass = [c for c in cands if (c.get('verdicts') or {}).get('P_B_plus_slip_vel') == 'PASS']
print(f'  ({len(p_pass)} candidates passed P_B_plus_slip_vel)')
print()
print(f'{"variant":<30} {"n":>5} {"WR%":>5} {"avg%":>7} {"TP%":>5} {"Stop%":>6} {"total%":>9}')
for name, key in [('baseline (current live)', 'phantom_pnl_pct'),
                   ('smart_bearflip', 'phantom_pnl_pct_smart_bearflip'),
                   ('TP1 sells 100%', 'phantom_pnl_pct_tp1_100pct')]:
    pnls = [c[key] for c in p_pass if c.get(key) is not None]
    s = stats(pnls)
    if not s:
        continue
    print(f'  {name:<30} {s["n"]:>5} {s["wr"]:>4.0f}% {s["avg_pct"]:>+5.2f}% '
          f'{s["tp_rate"]:>4.0f}% {s["stop_rate"]:>5.0f}% {s["total_pct"]:>+8.1f}%')

# Pairwise diff on each candidate
print()
print('=== Per-candidate diffs (where both baseline and shadow exit fired differently) ===')
sbf_better = 0
sbf_worse = 0
sbf_same = 0
sbf_diffs = []
for c in cands:
    b = c.get('phantom_pnl_pct')
    s = c.get('phantom_pnl_pct_smart_bearflip')
    if b is None or s is None:
        continue
    diff = s - b
    if abs(diff) < 0.01:
        sbf_same += 1
    elif diff > 0:
        sbf_better += 1
        sbf_diffs.append(diff)
    else:
        sbf_worse += 1
        sbf_diffs.append(diff)
print(f'smart_bearflip vs baseline: better={sbf_better} '
      f'worse={sbf_worse} same={sbf_same}')
if sbf_diffs:
    avg_diff = sum(sbf_diffs) / len(sbf_diffs)
    total_diff = sum(sbf_diffs)
    print(f'  Avg diff (when changed): {avg_diff:+.2f}% per trade, '
          f'total diff: {total_diff:+.1f}% across {len(sbf_diffs)} changes')

tp1_better = 0
tp1_worse = 0
tp1_same = 0
tp1_diffs = []
for c in cands:
    b = c.get('phantom_pnl_pct')
    t = c.get('phantom_pnl_pct_tp1_100pct')
    if b is None or t is None:
        continue
    diff = t - b
    if abs(diff) < 0.01:
        tp1_same += 1
    elif diff > 0:
        tp1_better += 1
        tp1_diffs.append(diff)
    else:
        tp1_worse += 1
        tp1_diffs.append(diff)
print(f'TP1 100% vs baseline: better={tp1_better} '
      f'worse={tp1_worse} same={tp1_same}')
if tp1_diffs:
    avg_diff = sum(tp1_diffs) / len(tp1_diffs)
    total_diff = sum(tp1_diffs)
    print(f'  Avg diff (when changed): {avg_diff:+.2f}% per trade, '
          f'total diff: {total_diff:+.1f}% across {len(tp1_diffs)} changes')

# Exit-reason breakdown for smart_bearflip
print()
print('=== smart_bearflip exit reasons distribution (among PASS candidates) ===')
sbf_reasons = Counter()
for c in cands:
    r = c.get('phantom_exit_reason_smart_bearflip')
    if r:
        sbf_reasons[r.split('(')[0].strip()] += 1
for r, n in sbf_reasons.most_common(8):
    print(f'  {r:<40} n={n}')
