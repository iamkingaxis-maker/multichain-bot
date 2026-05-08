"""Deep dive on clean_break — pull every feature, find the discriminator.

Strip out trades already addressed by tonight's gates:
  - dev<1 (GMAR)
  - peak>=1500 AND vs<0.30 (mask)
  - PENGUIN bug (pair-pinning fixed)

Then look at the RESIDUAL losers vs winners across every available feature.
"""
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
    if 'penguin' in (b.get('token') or '').lower():
        continue  # bug-driven, exclude

    # Apply tonight's already-shipped gates retroactively
    dev = em.get('dev_pct_remaining')
    peak = em.get('peak_h24_6h_pct')
    vs = em.get('1m_volume_spike')
    if dev is not None and dev < 1.0:
        continue  # already gated
    if peak is not None and peak >= 1500 and vs is not None and vs < 0.30:
        continue  # already gated

    cb_trades.append({
        'tok': b.get('token'),
        'pnl': pnl,
        'reason': (rel[-1].get('reason') or '').split('[')[0].strip(),
        # Pull every numeric feature available
        **{k: v for k, v in em.items()
           if isinstance(v, (int, float)) or
           (isinstance(v, str) and v.replace('-', '').replace('.', '').isdigit())},
    })

print(f'Residual clean_break trades after gates: {len(cb_trades)}')
wins = [t for t in cb_trades if t['pnl'] > 0]
losses = [t for t in cb_trades if t['pnl'] <= 0]
print(f'Wins: {len(wins)} (${sum(t["pnl"] for t in wins):+.2f})')
print(f'Losses: {len(losses)} (${sum(t["pnl"] for t in losses):+.2f})')
print()

# Discover every feature key
feat_keys = set()
for t in cb_trades:
    for k, v in t.items():
        if k in ('tok', 'pnl', 'reason'):
            continue
        try:
            float(v)
            feat_keys.add(k)
        except (ValueError, TypeError):
            pass

print(f'Available numeric features: {len(feat_keys)}')
print()


def get_vals(grp, key):
    out = []
    for t in grp:
        v = t.get(key)
        if v is None:
            continue
        try:
            out.append(float(v))
        except (ValueError, TypeError):
            pass
    return out


# Cohen's d-style separation
print('=== Feature separation (sorted by |median diff|) ===')
print(f'{"feature":<35} {"win_med":>9} {"loss_med":>10} {"diff":>8}')
rows = []
for k in feat_keys:
    wv = get_vals(wins, k)
    lv = get_vals(losses, k)
    if len(wv) < 4 or len(lv) < 3:
        continue
    wm, lm = median(wv), median(lv)
    diff = wm - lm
    if abs(lm) > 1:
        ratio = wm / lm if lm != 0 else 0
    else:
        ratio = 0
    rows.append((k, wm, lm, diff, len(wv), len(lv)))

rows.sort(key=lambda r: -abs(r[3]))
for k, wm, lm, diff, wn, ln in rows[:30]:
    marker = ''
    if abs(diff) > 0.5 and abs(diff) > min(abs(wm), abs(lm)) * 0.3:
        marker = '<<'
    print(f'  {k:<35} {wm:>+8.2f} {lm:>+9.2f} {diff:>+7.2f} {marker}')

# Now run gate tests on the most promising features
print()
print('=== Gate tests on residual cohort ===')


def block_test(name, pred):
    bl = [t for t in cb_trades if pred(t)]
    kp = [t for t in cb_trades if not pred(t)]
    if not bl:
        return
    bl_w = sum(1 for t in bl if t['pnl'] > 0)
    bl_l = sum(1 for t in bl if t['pnl'] <= 0)
    bl_total = sum(t['pnl'] for t in bl)
    kp_total = sum(t['pnl'] for t in kp)
    swing = -bl_total
    print(f'  BLOCK {name:<48} blocked={len(bl):>2} ({bl_w}W/{bl_l}L) ${bl_total:+6.2f} | '
          f'kept_total=${kp_total:+6.2f} | swing=${swing:+5.2f}')


def f(t, k, default=None):
    v = t.get(k)
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


# Focus on residual losses (AMERICA, SCAM, oGNOME, Hantavax)
print('=== RESIDUAL LOSSES (post-existing-gates) — what features do they share? ===')
print(f'{"pnl":>7} {"tok":<14} {"vs":>5} {"lc":>6} {"cum3":>6} {"bs_m5":>5} {"bs_h1":>5} {"bs_h6":>5} '
      f'{"peak":>6} {"mins":>5} {"liq":>7} {"dev":>5} {"reg":>5}')
for t in sorted(losses, key=lambda x: x['pnl']):
    print(f'  ${t["pnl"]:>+5.2f} {t["tok"]:<14} '
          f'{f(t,"1m_volume_spike",0):>5.2f} '
          f'{f(t,"1m_last_close_pct",0):>+5.2f} '
          f'{f(t,"1m_cum_3min_pct",0):>+5.2f} '
          f'{f(t,"bs_m5",0):>5.2f} '
          f'{f(t,"bs_h1",0):>5.2f} '
          f'{f(t,"bs_h6",0):>5.2f} '
          f'{f(t,"peak_h24_6h_pct",0):>5.0f} '
          f'{f(t,"minutes_since_peak",0):>5.0f} '
          f'{f(t,"liquidity_usd",0):>7.0f} '
          f'{f(t,"dev_pct_remaining",0):>5.1f} '
          f'{f(t,"regime_dip_breadth_pct",0):>5.1f}')

print()
print('=== WINNERS for comparison ===')
for t in sorted(wins, key=lambda x: -x['pnl'])[:15]:
    print(f'  ${t["pnl"]:>+5.2f} {t["tok"]:<14} '
          f'{f(t,"1m_volume_spike",0):>5.2f} '
          f'{f(t,"1m_last_close_pct",0):>+5.2f} '
          f'{f(t,"1m_cum_3min_pct",0):>+5.2f} '
          f'{f(t,"bs_m5",0):>5.2f} '
          f'{f(t,"bs_h1",0):>5.2f} '
          f'{f(t,"bs_h6",0):>5.2f} '
          f'{f(t,"peak_h24_6h_pct",0):>5.0f} '
          f'{f(t,"minutes_since_peak",0):>5.0f} '
          f'{f(t,"liquidity_usd",0):>7.0f} '
          f'{f(t,"dev_pct_remaining",0):>5.1f} '
          f'{f(t,"regime_dip_breadth_pct",0):>5.1f}')

# Now test specific gates
print()
print('=== Specific gates on RESIDUAL cohort ===')
# Very low vs (extending the gate)
block_test('vs<0.10', lambda t: f(t, '1m_volume_spike', 1) < 0.10)
block_test('vs<0.15', lambda t: f(t, '1m_volume_spike', 1) < 0.15)
block_test('vs<0.20', lambda t: f(t, '1m_volume_spike', 1) < 0.20)
block_test('vs<0.25', lambda t: f(t, '1m_volume_spike', 1) < 0.25)
# bs_h6 (longer-term buyer floor)
block_test('bs_h6<1.10', lambda t: f(t, 'bs_h6', 5) < 1.10)
block_test('bs_h6<1.15', lambda t: f(t, 'bs_h6', 5) < 1.15)
block_test('bs_h6<1.20', lambda t: f(t, 'bs_h6', 5) < 1.20)
block_test('bs_h6<1.25', lambda t: f(t, 'bs_h6', 5) < 1.25)
# liq
block_test('liq<50k', lambda t: f(t, 'liquidity_usd', 1e9) < 50000)
block_test('liq<100k', lambda t: f(t, 'liquidity_usd', 1e9) < 100000)
# minutes since peak — earlier we saw fresh peaks WIN
block_test('minutes_since_peak>=120', lambda t: f(t, 'minutes_since_peak', 0) >= 120)
block_test('minutes_since_peak>=180', lambda t: f(t, 'minutes_since_peak', 0) >= 180)
# Combos
block_test('vs<0.20 AND bs_h6<1.30',
           lambda t: f(t, '1m_volume_spike', 1) < 0.20 and f(t, 'bs_h6', 5) < 1.30)
block_test('vs<0.25 AND bs_h6<1.20',
           lambda t: f(t, '1m_volume_spike', 1) < 0.25 and f(t, 'bs_h6', 5) < 1.20)
block_test('vs<0.20 AND mins_peak<120',
           lambda t: f(t, '1m_volume_spike', 1) < 0.20 and f(t, 'minutes_since_peak', 9999) < 120)
block_test('vs<0.20 AND peak<200',
           lambda t: f(t, '1m_volume_spike', 1) < 0.20 and f(t, 'peak_h24_6h_pct', 0) < 200)
block_test('vs<0.25 AND peak<200 AND bs_h6<1.30',
           lambda t: f(t, '1m_volume_spike', 1) < 0.25 and f(t, 'peak_h24_6h_pct', 0) < 200
           and f(t, 'bs_h6', 5) < 1.30)
block_test('liq<100k AND bs_h6<1.20',
           lambda t: f(t, 'liquidity_usd', 1e9) < 100000 and f(t, 'bs_h6', 5) < 1.20)
block_test('regime_dip_breadth<10',
           lambda t: f(t, 'regime_dip_breadth_pct', 99) < 10)
