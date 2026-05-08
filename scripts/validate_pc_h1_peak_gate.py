"""Validate proposed gate: BLOCK clean_break when 1h_change <= -10 AND peak >= 200.

AMERICA + SELLOR pattern: post-pump distribution where 1m structure
suggests bounce but 1h trajectory is deeply down.
"""
import json
import re
import urllib.request
from collections import defaultdict


tr = json.loads(urllib.request.urlopen(
    'https://gracious-inspiration-production.up.railway.app/api/trades').read())
trades = tr if isinstance(tr, list) else tr.get('trades', [])
buys = [t for t in trades if t.get('type') == 'buy' and t.get('strategy') == 'dip_buy']
sells = [t for t in trades if t.get('type') == 'sell']
sell_idx = defaultdict(list)
for s in sells:
    sell_idx[(s.get('address'), s.get('pair_address'))].append(s)

TODAY_CUT = '2026-05-07T00:00:00'
PC_H1_RE = re.compile(r'1h=([+-]?\d+\.?\d*)%')

cb_trades = []
for b in buys:
    bt = b.get('time') or ''
    if bt < TODAY_CUT:
        continue
    em = b.get('entry_meta') or {}
    if em.get('trigger_source') != 'clean_break':
        continue
    rel = sorted(
        [s for s in sell_idx.get((b.get('address'), b.get('pair_address')), [])
         if (s.get('time') or '') > bt],
        key=lambda s: s.get('time') or ''
    )
    if not rel:
        continue
    pnl = sum(float(s.get('pnl') or 0) for s in rel)
    if abs(pnl) < 0.01 and 'cancel' in (rel[-1].get('reason') or '').lower():
        continue

    # Extract pc_h1 — try em first, then fall back to parsing reason
    pc_h1 = em.get('pc_h1')
    if pc_h1 is None:
        m = PC_H1_RE.search(b.get('reason') or '')
        if m:
            try:
                pc_h1 = float(m.group(1))
            except ValueError:
                pc_h1 = None

    cb_trades.append({
        'tok': b.get('token'), 'pnl': pnl,
        'pc_h1': pc_h1,
        'peak': em.get('peak_h24_6h_pct'),
        'reason': (rel[-1].get('reason') or '').split('[')[0].strip(),
    })

print(f'clean_break trades today: {len(cb_trades)}')
print()


def gate(t):
    """Returns True if proposed gate would BLOCK this trade."""
    return (t['pc_h1'] is not None and t['pc_h1'] <= -10
            and t['peak'] is not None and t['peak'] >= 200)


blocked = [t for t in cb_trades if gate(t)]
kept = [t for t in cb_trades if not gate(t)]

print('=== Proposed gate: 1h_change<=-10 AND peak>=200 ===')
print(f'BLOCKED ({len(blocked)} trades, total ${sum(t["pnl"] for t in blocked):+.2f})')
for t in sorted(blocked, key=lambda x: x['pnl']):
    print(f'  ${t["pnl"]:>+5.2f} {t["tok"]:<14} '
          f'pc_h1={t["pc_h1"]} peak={t["peak"]} -- {t["reason"]}')
print()
print(f'KEPT ({len(kept)} trades, total ${sum(t["pnl"] for t in kept):+.2f})')
wins_kept = [t for t in kept if t['pnl'] > 0]
losses_kept = [t for t in kept if t['pnl'] <= 0]
print(f'  Wins: {len(wins_kept)}, Losses: {len(losses_kept)}')

print()
print('=== Counterfactual: lifetime (today) clean_break with gate ===')
total_now = sum(t['pnl'] for t in cb_trades)
total_blocked = sum(t['pnl'] for t in blocked)
total_kept = sum(t['pnl'] for t in kept)
print(f'Current clean_break total today: ${total_now:+.2f}')
print(f'Blocked PnL (saved if negative): ${total_blocked:+.2f}')
print(f'Net swing: ${-total_blocked:+.2f}')
print(f'Kept-only total: ${total_kept:+.2f}')

print()
print('=== Try variant thresholds ===')
for h1_thr, peak_thr in [(-5, 200), (-10, 200), (-15, 200), (-10, 100), (-15, 300)]:
    bl = [t for t in cb_trades
          if t['pc_h1'] is not None and t['pc_h1'] <= h1_thr
          and t['peak'] is not None and t['peak'] >= peak_thr]
    kept2 = [t for t in cb_trades if t not in bl]
    bl_w = sum(1 for t in bl if t['pnl'] > 0)
    bl_l = sum(1 for t in bl if t['pnl'] <= 0)
    kept_total = sum(t['pnl'] for t in kept2)
    bl_total = sum(t['pnl'] for t in bl)
    swing = -bl_total
    print(f'  h1<={h1_thr} AND peak>={peak_thr}: '
          f'blocked={len(bl)} ({bl_w}W/{bl_l}L) ${bl_total:+.2f} | '
          f'kept_total=${kept_total:+.2f} | swing=${swing:+.2f}')

# Show all clean_break trades with pc_h1 + peak for context
print()
print('=== ALL clean_break trades sorted by pnl (with pc_h1, peak) ===')
print(f'{"pnl":>7} {"tok":<14} {"pc_h1":>6} {"peak":>6}')
for t in sorted(cb_trades, key=lambda x: x['pnl']):
    h1 = f'{t["pc_h1"]:+5.1f}%' if t['pc_h1'] is not None else '?'
    pk = f'{t["peak"]:.0f}%' if t['peak'] is not None else '?'
    print(f'  ${t["pnl"]:>+5.2f} {t["tok"]:<14} {h1:>7} {pk:>7}')
