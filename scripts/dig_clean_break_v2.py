"""Round 2 dig — test gates on the high-separation features:
  hours_since_graduation, cycles_seen_before_buy, time_since_peak, regime_dip_breadth.
"""
import json
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
    if 'penguin' in (b.get('token') or '').lower():
        continue

    # Apply tonight's gates retroactively
    dev = em.get('dev_pct_remaining')
    peak = em.get('peak_h24_6h_pct')
    vs = em.get('1m_volume_spike')
    if dev is not None and dev < 1.0:
        continue
    if peak is not None and peak >= 1500 and vs is not None and vs < 0.30:
        continue

    cb_trades.append({
        'tok': b.get('token'),
        'pnl': pnl,
        'em': em,
    })


def f(t, k, default=None):
    v = (t['em']).get(k)
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def block_test(name, pred):
    bl = [t for t in cb_trades if pred(t)]
    kp = [t for t in cb_trades if not pred(t)]
    if not bl:
        print(f'  BLOCK {name:<55}  0 blocked')
        return
    bl_w = sum(1 for t in bl if t['pnl'] > 0)
    bl_l = sum(1 for t in bl if t['pnl'] <= 0)
    bl_total = sum(t['pnl'] for t in bl)
    kp_total = sum(t['pnl'] for t in kp)
    swing = -bl_total
    star = ' ★' if swing > 0 and bl_l > bl_w else ('  ' if swing > 0 else '')
    print(f'  BLOCK {name:<55} blocked={len(bl):>2} ({bl_w}W/{bl_l}L) ${bl_total:+6.2f} | '
          f'kept=${kp_total:+6.2f} | swing=${swing:+5.2f}{star}')


print(f'Residual cohort: {len(cb_trades)} ({sum(1 for t in cb_trades if t["pnl"]>0)}W/'
      f'{sum(1 for t in cb_trades if t["pnl"]<=0)}L)')
print()
print('Total available capital:')
print(f'  Wins: ${sum(t["pnl"] for t in cb_trades if t["pnl"] > 0):+.2f}')
print(f'  Losses: ${sum(t["pnl"] for t in cb_trades if t["pnl"] <= 0):+.2f}')
print()

# Per-trade snapshot of the top candidates
print('=== Each residual trade with target features ===')
print(f'{"pnl":>7} {"tok":<14} {"hsg":>6} {"cyc":>4} {"mins_pk":>7} {"reg_db":>6} {"5m_pat":>6}')
for t in sorted(cb_trades, key=lambda x: x['pnl']):
    print(f'  ${t["pnl"]:>+5.2f} {t["em"].get("token_symbol", t["tok"])[:14]:<14} '
          f'{f(t,"hours_since_graduation",-1):>6.1f} '
          f'{f(t,"cycles_seen_before_buy",-1):>4.0f} '
          f'{f(t,"minutes_since_peak",-1):>7.1f} '
          f'{f(t,"regime_dip_breadth_pct",-1):>6.1f} '
          f'{f(t,"chart_pattern_5m_conf",-1):>6.1f}')

print()
print('=== Single feature gates (★ = strict win — saves more loss than kills wins) ===')
block_test('hours_since_graduation>=72', lambda t: f(t, 'hours_since_graduation', 0) >= 72)
block_test('hours_since_graduation>=100', lambda t: f(t, 'hours_since_graduation', 0) >= 100)
block_test('hours_since_graduation>=150', lambda t: f(t, 'hours_since_graduation', 0) >= 150)
block_test('hours_since_graduation>=200', lambda t: f(t, 'hours_since_graduation', 0) >= 200)
block_test('cycles_seen_before_buy>=60', lambda t: f(t, 'cycles_seen_before_buy', 0) >= 60)
block_test('cycles_seen_before_buy>=80', lambda t: f(t, 'cycles_seen_before_buy', 0) >= 80)
block_test('cycles_seen_before_buy>=100', lambda t: f(t, 'cycles_seen_before_buy', 0) >= 100)
block_test('regime_dip_breadth<10', lambda t: f(t, 'regime_dip_breadth_pct', 99) < 10)
block_test('regime_dip_breadth<11', lambda t: f(t, 'regime_dip_breadth_pct', 99) < 11)
block_test('chart_pattern_5m_conf>=80', lambda t: f(t, 'chart_pattern_5m_conf', 0) >= 80)
block_test('minutes_since_peak>=180', lambda t: f(t, 'minutes_since_peak', 0) >= 180)

print()
print('=== Combos ===')
block_test('hsg>=100 AND cycles>=60',
           lambda t: f(t, 'hours_since_graduation', 0) >= 100 and f(t, 'cycles_seen_before_buy', 0) >= 60)
block_test('hsg>=72 AND cycles>=60',
           lambda t: f(t, 'hours_since_graduation', 0) >= 72 and f(t, 'cycles_seen_before_buy', 0) >= 60)
block_test('hsg>=72 AND mins_peak>=60',
           lambda t: f(t, 'hours_since_graduation', 0) >= 72 and f(t, 'minutes_since_peak', 0) >= 60)
block_test('hsg>=100 OR mins_peak>=180',
           lambda t: f(t, 'hours_since_graduation', 0) >= 100 or f(t, 'minutes_since_peak', 0) >= 180)
block_test('hsg>=72 AND mins_peak>=120',
           lambda t: f(t, 'hours_since_graduation', 0) >= 72 and f(t, 'minutes_since_peak', 0) >= 120)
block_test('cycles>=80 AND mins_peak>=60',
           lambda t: f(t, 'cycles_seen_before_buy', 0) >= 80 and f(t, 'minutes_since_peak', 0) >= 60)

print()
print('=== Combine with regime ===')
block_test('regime<11 OR hsg>=150',
           lambda t: f(t, 'regime_dip_breadth_pct', 99) < 11
           or f(t, 'hours_since_graduation', 0) >= 150)
block_test('regime<11 OR cycles>=100',
           lambda t: f(t, 'regime_dip_breadth_pct', 99) < 11
           or f(t, 'cycles_seen_before_buy', 0) >= 100)

print()
print('=== Cleanest wins (combined heuristic) ===')
# A "stale dump" composite: all of (old + scanned-many-times + far-from-peak)
block_test('STALE: hsg>=100 AND cycles>=80 AND mins_peak>=60',
           lambda t: (f(t, 'hours_since_graduation', 0) >= 100
                      and f(t, 'cycles_seen_before_buy', 0) >= 80
                      and f(t, 'minutes_since_peak', 0) >= 60))
