import sys, statistics as st
sys.argv = ['x']
exec(open('scratchpad/sol_aged_pond/analyze.py').read())

# Universe: absorb-family, aged 6-24h (the pond)
POND = [t for t in AGED if 'absorb' in t['bot'] and t.get('lifecycle_age_h') is not None
        and 6 <= t['lifecycle_age_h'] < 24]
POND24 = [t for t in AGED if 'absorb' in t['bot'] and t.get('lifecycle_age_h') is not None
          and 24 <= t['lifecycle_age_h'] < 96]
print(f"POND 6-24h absorb-family: {len(POND)} legs, {len(set(t['address'] for t in POND))} tok")
print(f"POND 24-96h absorb-family: {len(POND24)} legs, {len(set(t['address'] for t in POND24))} tok")
print()
report('POND 6-24h base', POND)
report('POND 24-96h base', POND24)
print()

def oos_pass(trips, thr=-2.0):
    """returns min ex2 across 4 halves and whether all halves have n>=8 and ex2>=thr"""
    c1, c2 = halves_chrono(trips); o, e = halves_oddeven(trips)
    res = []
    for tr in (c1, c2, o, e):
        tm, n, pg = ex_top2(tr)
        res.append((tm, n))
    return res

CANDS = [
    ('pc_h6<0 (post-pump)', lambda t: t.get('pc_h6') is not None and t['pc_h6'] < 0),
    ('pc_h6<-20', lambda t: t.get('pc_h6') is not None and t['pc_h6'] < -20),
    ('pc_h1<=-35 (deep cap)', lambda t: t.get('pc_h1') is not None and t['pc_h1'] <= -35),
    ('pc_h1<=-40', lambda t: t.get('pc_h1') is not None and t['pc_h1'] <= -40),
    ('nf15_imbal>=0.2', lambda t: t.get('nf15_imbal') is not None and t['nf15_imbal'] >= 0.2),
    ('nf15_imbal>=0.4', lambda t: t.get('nf15_imbal') is not None and t['nf15_imbal'] >= 0.4),
    ('liq>=35k', lambda t: t.get('liq') is not None and t['liq'] >= 35000),
    ('liq>=45k', lambda t: t.get('liq') is not None and t['liq'] >= 45000),
    ('buyers>=30', lambda t: t.get('unique_buyers_n') is not None and t['unique_buyers_n'] >= 30),
    ('buyers>=50', lambda t: t.get('unique_buyers_n') is not None and t['unique_buyers_n'] >= 50),
    ('median_buy>=12', lambda t: t.get('median_buy_usd') is not None and t['median_buy_usd'] >= 12),
    ('mean_buy>=40', lambda t: t.get('mean_buy_usd') is not None and t['mean_buy_usd'] >= 40),
    ('rsi_5m<40', lambda t: t.get('rsi_5m') is not None and t['rsi_5m'] < 40),
    ('bb_pos_5m<0.3', lambda t: t.get('bb_pos_5m') is not None and t['bb_pos_5m'] < 0.3),
    ('lower_wick_5m>=0.5', lambda t: t.get('lower_wick_ratio_5m') is not None and t['lower_wick_ratio_5m'] >= 0.5),
    ('hidden_supply<60', lambda t: t.get('hidden_supply_pct') is not None and t['hidden_supply_pct'] < 60),
    ('top10<25', lambda t: t.get('top10_holder_pct') is not None and t['top10_holder_pct'] < 25),
    ('vol_h24>=1M', lambda t: t.get('entry_vol_h24') is not None and t['entry_vol_h24'] >= 1e6),
    ('buy_pressure>=0.6', lambda t: t.get('buy_pressure_60s') is not None and t['buy_pressure_60s'] >= 0.6),
    ('pct_above_support>=10', lambda t: t.get('pct_above_support') is not None and t['pct_above_support'] >= 10),
]

print("=== Q3: single-axis cohorts on POND 6-24h (need ex2>0, tokGrn>=50, n>=15) ===")
winners = []
for name, fn in CANDS:
    sub = [t for t in POND if fn(t)]
    tm, n, pg = ex_top2(sub)
    if n < 8:
        continue
    flag = ''
    if tm is not None and tm > 0 and pg is not None and pg >= 50 and n >= 15:
        flag = ' <<< GREEN'
        winners.append((name, fn, tm, n, pg))
    report(name, sub)
    print(flag) if flag else None

print("\n=== 2-way combos with pc_h6<0 base (post-pump) ===")
base = lambda t: t.get('pc_h6') is not None and t['pc_h6'] < 0
POND_PP = [t for t in POND if base(t)]
report('POND 6-24h & pc_h6<0', POND_PP)
for name, fn in CANDS:
    if 'pc_h6' in name:
        continue
    sub = [t for t in POND_PP if fn(t)]
    tm, n, pg = ex_top2(sub)
    if n < 12:
        continue
    flag = ' <<< GREEN' if (tm is not None and tm > 0 and pg is not None and pg >= 50 and n >= 15) else ''
    report('  pc_h6<0 & ' + name, sub)
    if flag:
        print(flag)
        winners.append(('pc_h6<0 & ' + name, lambda t, f=fn: base(t) and f(t), tm, n, pg))

print("\n=== OOS check on green cohorts ===")
for name, fn, tm, n, pg in winners:
    sub = [t for t in POND if fn(t)]
    print(f"\n{name}: base ex2={tm:+.1f} n={n} grn={pg:.0f}%")
    for hl, tr in zip(['chrono-E', 'chrono-L', 'odd', 'even'],
                      [halves_chrono(sub)[0], halves_chrono(sub)[1], halves_oddeven(sub)[0], halves_oddeven(sub)[1]]):
        t2, nn, gg = ex_top2(tr)
        f = f'{t2:+.1f}' if t2 is not None else '-'
        print(f"  {hl:<9} ex2={f:>6} n={nn}")
