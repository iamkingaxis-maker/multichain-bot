"""Analyze today's clean_break trades to find a discriminator."""
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
    cb_trades.append({
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

print(f'clean_break trades today: {len(cb_trades)}')
wins = [t for t in cb_trades if t['pnl'] > 0]
losses = [t for t in cb_trades if t['pnl'] <= 0]
print(f'Wins: {len(wins)}, Losses: {len(losses)}')
print(f'Win total: ${sum(t["pnl"] for t in wins):+.2f}')
print(f'Loss total: ${sum(t["pnl"] for t in losses):+.2f}')
print(f'Net: ${sum(t["pnl"] for t in cb_trades):+.2f}')
print()


def fmt(v, w, p):
    if v is None:
        return ' ' * w
    try:
        return f'{float(v):>{w}.{p}f}'
    except (TypeError, ValueError):
        return str(v)[:w].rjust(w)


print('=== ALL clean_break trades sorted by pnl ===')
print(f'{"pnl":>7} {"tok":<14} {"5mR":>3} {"5mG":>3} {"lc%":>6} {"vs":>5} '
      f'{"cum3":>6} {"bs_m5":>5} {"bs_h6":>5} {"peak":>6} {"mins":>5} '
      f'{"liq":>7} {"dev%":>5}')
for t in sorted(cb_trades, key=lambda x: x['pnl']):
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
         'mins_peak', 'liq', 'dev', '5m_red', '5m_grn', '5m_red_count'):
    wv = get_vals(wins, f)
    lv = get_vals(losses, f)
    if not wv or not lv:
        continue
    wm, lm = median(wv), median(lv)
    sep = ''
    if wm > lm * 1.5 and abs(wm - lm) > 0.1:
        sep = 'WIN > LOSS'
    elif lm > wm * 1.5 and abs(lm - wm) > 0.1:
        sep = 'LOSS > WIN'
    print(f'  {f:<14} {wm:>+9.3f} {lm:>+10.3f}    {sep}')

print()
print('=== Gate tests (KEEP if predicate True) ===')


def apply_gate(name, pred):
    keep = [t for t in cb_trades if pred(t)]
    drop = [t for t in cb_trades if not pred(t)]
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


apply_gate('dev>=2.0', lambda t: (t['dev'] or 0) >= 2.0)
apply_gate('dev>=5.0', lambda t: (t['dev'] or 0) >= 5.0)
apply_gate('vs>=0.5', lambda t: t['vs'] is not None and t['vs'] >= 0.5)
apply_gate('vs>=0.7', lambda t: t['vs'] is not None and t['vs'] >= 0.7)
apply_gate('vs>=1.0', lambda t: t['vs'] is not None and t['vs'] >= 1.0)
apply_gate('peak<300', lambda t: t['peak'] is not None and t['peak'] < 300)
apply_gate('peak<200', lambda t: t['peak'] is not None and t['peak'] < 200)
apply_gate('peak<1500', lambda t: t['peak'] is not None and t['peak'] < 1500)
apply_gate('mins_peak>=30', lambda t: (t['mins_peak'] or 0) >= 30)
apply_gate('mins_peak>=15', lambda t: (t['mins_peak'] or 0) >= 15)
apply_gate('cum3>=0', lambda t: t['cum3'] is not None and t['cum3'] >= 0)
apply_gate('cum3>=-2', lambda t: t['cum3'] is not None and t['cum3'] >= -2)
apply_gate('cum3>=-5', lambda t: t['cum3'] is not None and t['cum3'] >= -5)
apply_gate('cum3>=2', lambda t: t['cum3'] is not None and t['cum3'] >= 2)
apply_gate('liq>=100k', lambda t: t['liq'] is not None and t['liq'] >= 100000)
apply_gate('liq>=200k', lambda t: t['liq'] is not None and t['liq'] >= 200000)
apply_gate('bs_h6>=2.0', lambda t: t['bs_h6'] is not None and t['bs_h6'] >= 2.0)
apply_gate('bs_m5>=1.0', lambda t: t['bs_m5'] is not None and t['bs_m5'] >= 1.0)
apply_gate('bs_m5>=1.4', lambda t: t['bs_m5'] is not None and t['bs_m5'] >= 1.4)
apply_gate('5m_grn>=1', lambda t: (t['5m_grn'] or 0) >= 1)
apply_gate('lc>=0.5', lambda t: t['lc'] is not None and t['lc'] >= 0.5)
apply_gate('lc>=1.0', lambda t: t['lc'] is not None and t['lc'] >= 1.0)
print()
print('--- Suppression gates (BLOCK if predicate True) ---')


def block_gate(name, pred):
    blocked = [t for t in cb_trades if pred(t)]
    kept = [t for t in cb_trades if not pred(t)]
    if not blocked:
        print(f'  BLOCK {name:<42} 0 blocked')
        return
    b_pnl = sum(t['pnl'] for t in blocked)
    k_pnl = sum(t['pnl'] for t in kept)
    b_w = sum(1 for t in blocked if t['pnl'] > 0)
    b_l = sum(1 for t in blocked if t['pnl'] <= 0)
    k_w = sum(1 for t in kept if t['pnl'] > 0)
    k_l = sum(1 for t in kept if t['pnl'] <= 0)
    print(f'  BLOCK {name:<42} blocked={len(blocked):>2} ({b_w}W/{b_l}L) ${b_pnl:>+6.2f} | '
          f'kept={len(kept):>2} ({k_w}W/{k_l}L) ${k_pnl:>+6.2f}')


block_gate('peak>=1500 AND vs<0.10',
           lambda t: t['peak'] is not None and t['peak'] >= 1500
           and t['vs'] is not None and t['vs'] < 0.10)
block_gate('peak>=1500 AND vs<0.30',
           lambda t: t['peak'] is not None and t['peak'] >= 1500
           and t['vs'] is not None and t['vs'] < 0.30)
block_gate('vs<0.20 AND cum3<0',
           lambda t: t['vs'] is not None and t['vs'] < 0.20
           and t['cum3'] is not None and t['cum3'] < 0)
block_gate('vs<0.20 AND cum3<-2',
           lambda t: t['vs'] is not None and t['vs'] < 0.20
           and t['cum3'] is not None and t['cum3'] < -2)
block_gate('dev<2 (chronic dumper)',
           lambda t: t['dev'] is not None and t['dev'] < 2.0)
block_gate('dev<1 (severe dumper)',
           lambda t: t['dev'] is not None and t['dev'] < 1.0)
block_gate('cum3<-5 (sharp 3m down)',
           lambda t: t['cum3'] is not None and t['cum3'] < -5)
block_gate('cum3<-3 AND vs<0.30',
           lambda t: t['cum3'] is not None and t['cum3'] < -3
           and t['vs'] is not None and t['vs'] < 0.30)
block_gate('vs<0.20',
           lambda t: t['vs'] is not None and t['vs'] < 0.20)
block_gate('peak>=1500',
           lambda t: t['peak'] is not None and t['peak'] >= 1500)
block_gate('peak>=1500 AND cum3<0',
           lambda t: t['peak'] is not None and t['peak'] >= 1500
           and t['cum3'] is not None and t['cum3'] < 0)
