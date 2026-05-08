"""Analyze today's high_regime trades to find a discriminator."""
import json
import urllib.request
from collections import defaultdict
from statistics import median


tr = json.loads(urllib.request.urlopen(
    'https://gracious-inspiration-production.up.railway.app/api/trades').read())
trades = tr if isinstance(tr, list) else tr.get('trades', [])

buys = [t for t in trades if t.get('type') == 'buy' and t.get('strategy') == 'dip_buy']
sells = [t for t in trades if t.get('type') == 'sell']
sell_idx = defaultdict(list)
for s in sells:
    sell_idx[(s.get('address'), s.get('pair_address'))].append(s)

TODAY_CUT = '2026-05-07T00:00:00'
hr_trades = []
for b in buys:
    bt = b.get('time') or ''
    if bt < TODAY_CUT:
        continue
    em = b.get('entry_meta') or {}
    if em.get('trigger_source') != 'high_regime':
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
    hr_trades.append({
        'tok': b.get('token'), 'pnl': pnl,
        '5m_red': em.get('5m_consec_red') or 0,
        '5m_grn': em.get('5m_consec_green') or 0,
        '5m_red_count': em.get('5m_red_count') or 0,
        'lc': em.get('1m_last_close_pct'),
        'vs': em.get('1m_volume_spike'),
        'cum3': em.get('1m_cum_3min_pct'),
        'bs_m5': em.get('bs_m5'),
        'bs_h1': em.get('bs_h1'),
        'bs_h6': em.get('bs_h6'),
        'peak': em.get('peak_h24_6h_pct'),
        'mins_peak': em.get('minutes_since_peak'),
        'liq': em.get('liquidity_usd'),
        'dev': em.get('dev_pct_remaining'),
        'regime': em.get('regime_dip_breadth_pct'),
    })

print(f'high_regime trades today: {len(hr_trades)}')
wins = [t for t in hr_trades if t['pnl'] > 0]
losses = [t for t in hr_trades if t['pnl'] <= 0]
print(f'Wins: {len(wins)}, Losses: {len(losses)}')
print(f'Win total: ${sum(t["pnl"] for t in wins):+.2f}')
print(f'Loss total: ${sum(t["pnl"] for t in losses):+.2f}')
print(f'Net: ${sum(t["pnl"] for t in hr_trades):+.2f}')
print()

print('=== ALL high_regime trades sorted by pnl ===')
print(f'{"pnl":>7} {"tok":<14} {"5mR":>3} {"5mG":>3} {"lc%":>6} {"vs":>5} '
      f'{"cum3":>6} {"bs_m5":>5} {"bs_h6":>5} {"peak":>6} {"mins":>5} '
      f'{"liq":>7} {"dev%":>5}')


def fmt(v, w, p):
    if v is None:
        return ' ' * w
    try:
        return f'{float(v):>{w}.{p}f}'
    except (TypeError, ValueError):
        return str(v)[:w].rjust(w)


for t in sorted(hr_trades, key=lambda x: x['pnl']):
    print(f'  ${t["pnl"]:>+5.2f} {t["tok"]:<14} '
          f'{fmt(t["5m_red"],3,0)} {fmt(t["5m_grn"],3,0)} '
          f'{fmt(t["lc"],6,2)} {fmt(t["vs"],5,2)} {fmt(t["cum3"],6,2)} '
          f'{fmt(t["bs_m5"],5,2)} {fmt(t["bs_h6"],5,2)} '
          f'{fmt(t["peak"],6,0)} {fmt(t["mins_peak"],5,1)} '
          f'{fmt(t["liq"],7,0)} {fmt(t["dev"],5,1)}')

print()
print('=== Median features: WIN vs LOSS ===')
print(f'{"feature":<14} {"win_med":>9} {"loss_med":>10} {"sep":>14}')


def get_vals(grp, key):
    return [t[key] for t in grp if t[key] is not None]


for f in ('lc', 'vs', 'cum3', 'bs_m5', 'bs_h1', 'bs_h6', 'peak',
         'mins_peak', 'liq', 'dev', '5m_red', '5m_grn', '5m_red_count', 'regime'):
    wv = get_vals(wins, f)
    lv = get_vals(losses, f)
    if not wv or not lv:
        continue
    wm, lm = median(wv), median(lv)
    sep = ''
    if wm > lm * 1.5 and (wm - lm) > 0.1:
        sep = 'WIN > LOSS'
    elif lm > wm * 1.5 and (lm - wm) > 0.1:
        sep = 'LOSS > WIN'
    print(f'  {f:<14} {wm:>+9.3f} {lm:>+10.3f}    {sep}')

print()
print('=== Gate tests (KEEP if predicate True) ===')


def apply_gate(name, pred):
    keep = [t for t in hr_trades if pred(t)]
    drop = [t for t in hr_trades if not pred(t)]
    if not keep:
        print(f'  {name:<48} 0 kept')
        return
    keep_pnl = sum(t['pnl'] for t in keep)
    drop_pnl = sum(t['pnl'] for t in drop)
    keep_w = sum(1 for t in keep if t['pnl'] > 0)
    keep_l = sum(1 for t in keep if t['pnl'] <= 0)
    drop_w = sum(1 for t in drop if t['pnl'] > 0)
    drop_l = sum(1 for t in drop if t['pnl'] <= 0)
    keep_wr = keep_w / len(keep) * 100
    print(f'  {name:<48} kept={len(keep):>2} ({keep_w}W/{keep_l}L, '
          f'WR={keep_wr:.0f}%) ${keep_pnl:>+6.2f} | '
          f'dropped={len(drop):>2} ({drop_w}W/{drop_l}L) ${drop_pnl:>+6.2f}')


apply_gate('dev>=2.0', lambda t: t['dev'] is not None and t['dev'] >= 2.0)
apply_gate('dev>=5.0', lambda t: t['dev'] is not None and t['dev'] >= 5.0)
apply_gate('liq>=200k', lambda t: t['liq'] is not None and t['liq'] >= 200000)
apply_gate('liq>=150k', lambda t: t['liq'] is not None and t['liq'] >= 150000)
apply_gate('peak<300', lambda t: t['peak'] is not None and t['peak'] < 300)
apply_gate('peak<200', lambda t: t['peak'] is not None and t['peak'] < 200)
apply_gate('mins_peak>=30', lambda t: t['mins_peak'] is not None and t['mins_peak'] >= 30)
apply_gate('mins_peak>=15', lambda t: t['mins_peak'] is not None and t['mins_peak'] >= 15)
apply_gate('cum3>=2.0', lambda t: t['cum3'] is not None and t['cum3'] >= 2.0)
apply_gate('cum3>=4.0', lambda t: t['cum3'] is not None and t['cum3'] >= 4.0)
apply_gate('bs_h6>=2.0', lambda t: t['bs_h6'] is not None and t['bs_h6'] >= 2.0)
apply_gate('bs_h6>=5.0', lambda t: t['bs_h6'] is not None and t['bs_h6'] >= 5.0)
apply_gate('vs>=0.5', lambda t: t['vs'] is not None and t['vs'] >= 0.5)
apply_gate('vs>=0.7', lambda t: t['vs'] is not None and t['vs'] >= 0.7)
apply_gate('5m_grn>=1', lambda t: (t['5m_grn'] or 0) >= 1)
apply_gate('5m_red==0', lambda t: (t['5m_red'] or 0) == 0)
apply_gate('lc>=0', lambda t: t['lc'] is not None and t['lc'] >= 0)
print()
print('--- Combos ---')
apply_gate('dev>=2 AND liq>=150k',
           lambda t: (t['dev'] or 0) >= 2 and (t['liq'] or 0) >= 150000)
apply_gate('dev>=2 AND vs>=0.4',
           lambda t: (t['dev'] or 0) >= 2 and t['vs'] is not None and t['vs'] >= 0.4)
apply_gate('dev>=2 AND mins_peak>=15',
           lambda t: (t['dev'] or 0) >= 2 and (t['mins_peak'] or 0) >= 15)
apply_gate('dev>=5 AND vs>=0.4',
           lambda t: (t['dev'] or 0) >= 5 and t['vs'] is not None and t['vs'] >= 0.4)
apply_gate('dev>=2 AND 5m_red==0',
           lambda t: (t['dev'] or 0) >= 2 and (t['5m_red'] or 0) == 0)
apply_gate('dev>=2 AND 5m_grn>=1',
           lambda t: (t['dev'] or 0) >= 2 and (t['5m_grn'] or 0) >= 1)
apply_gate('dev>=2 AND bs_h6>=2',
           lambda t: (t['dev'] or 0) >= 2 and t['bs_h6'] is not None and t['bs_h6'] >= 2.0)
apply_gate('dev>=5 AND mins_peak>=10',
           lambda t: (t['dev'] or 0) >= 5 and (t['mins_peak'] or 0) >= 10)
apply_gate('dev>=5 AND 5m_grn>=1',
           lambda t: (t['dev'] or 0) >= 5 and (t['5m_grn'] or 0) >= 1)
apply_gate('dev>=2 AND vs>=0.5 AND 5m_grn>=1',
           lambda t: (t['dev'] or 0) >= 2 and t['vs'] is not None
           and t['vs'] >= 0.5 and (t['5m_grn'] or 0) >= 1)
apply_gate('dev>=2 AND cum3>=1',
           lambda t: (t['dev'] or 0) >= 2 and t['cum3'] is not None and t['cum3'] >= 1.0)
apply_gate('dev>=2 AND cum3>=4',
           lambda t: (t['dev'] or 0) >= 2 and t['cum3'] is not None and t['cum3'] >= 4.0)
