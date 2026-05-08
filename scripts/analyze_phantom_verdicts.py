"""Analyze phantom forward-test verdicts — which filter combos perform best?"""
import json
import glob
from collections import defaultdict


files = sorted(glob.glob('.live_forward_test/2026*.json'))
resolved_cands = []
for f in files:
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if not d.get('resolved'):
        continue
    for c in d.get('candidates') or []:
        if 'phantom_pnl_pct' not in c:
            continue
        resolved_cands.append(c)

print(f'Resolved phantom candidates: {len(resolved_cands)}')
print()

# Per-verdict aggregate
verdict_keys = set()
for c in resolved_cands:
    verdict_keys |= set((c.get('verdicts') or {}).keys())

results = {}
for v in sorted(verdict_keys):
    pas_pnl = []  # phantom pnl (% return) for candidates that PASSed this filter
    blk_pnl = []  # for candidates that BLOCKed
    for c in resolved_cands:
        verdict = (c.get('verdicts') or {}).get(v)
        pnl = c.get('phantom_pnl_usd')
        if pnl is None:
            continue
        if verdict == 'PASS':
            pas_pnl.append(pnl)
        elif verdict == 'BLOCK':
            blk_pnl.append(pnl)
    if not pas_pnl:
        continue
    pas_n = len(pas_pnl)
    pas_w = sum(1 for p in pas_pnl if p > 0)
    pas_total = sum(pas_pnl)
    pas_avg = pas_total / pas_n
    pas_wr = pas_w / pas_n * 100
    blk_avg = sum(blk_pnl) / max(1, len(blk_pnl))
    blk_total = sum(blk_pnl)
    results[v] = {
        'pass_n': pas_n, 'pass_w': pas_w, 'pass_wr': pas_wr,
        'pass_total': pas_total, 'pass_avg': pas_avg,
        'block_n': len(blk_pnl), 'block_total': blk_total, 'block_avg': blk_avg,
    }

# Print sorted by pass_avg desc
print(f'{"verdict":<32} {"pass_n":>6} {"WR%":>5} {"avg$":>7} {"total$":>9} '
      f'{"block_n":>7} {"blk_avg$":>8}')
print('-' * 90)
for v, r in sorted(results.items(), key=lambda x: -x[1]['pass_avg']):
    print(f'  {v:<32} {r["pass_n"]:>6} {r["pass_wr"]:>4.0f}% '
          f'${r["pass_avg"]:>+5.2f} ${r["pass_total"]:>+7.2f} '
          f'{r["block_n"]:>7} ${r["block_avg"]:>+6.2f}')

print()
print('=== Top verdicts by total P&L (high pass_n) ===')
print(f'{"verdict":<32} {"pass_n":>6} {"WR%":>5} {"total$":>9}')
candidates_for_ship = []
for v, r in sorted(results.items(), key=lambda x: -x[1]['pass_total']):
    if r['pass_n'] < 50:
        continue
    print(f'  {v:<32} {r["pass_n"]:>6} {r["pass_wr"]:>4.0f}% ${r["pass_total"]:>+7.2f}')
    candidates_for_ship.append((v, r))

print()
print('=== Best ratio (avg pass$ - avg block$) — most discriminating ===')
ranked = []
for v, r in results.items():
    if r['pass_n'] < 30 or r['block_n'] < 30:
        continue
    diff = r['pass_avg'] - r['block_avg']
    ranked.append((v, r, diff))
ranked.sort(key=lambda x: -x[2])
print(f'{"verdict":<32} {"pass_avg":>9} {"block_avg":>10} {"diff":>7}')
for v, r, diff in ranked[:15]:
    print(f'  {v:<32} ${r["pass_avg"]:>+7.2f} ${r["block_avg"]:>+8.2f} ${diff:>+5.2f}')
