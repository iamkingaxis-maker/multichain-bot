import json, statistics
d=json.load(open('_full_trades.json'))

# Winner trips: wallet -> list of (token10, ret_pct)
winners = {
 'DU25Xy':[('4HhWcsfjaW',36.2),('ERZrVfHz9D',-66.3),('EnqCw742Nb',791.8),('9S8edqWxoW',73314.5),('6cgUrnK8ix',178097.5),('wMNSTc9xcR',4.0),('217dt9idH1',18.7),('33eum82LaA',53.6),('yxqegfnoem',-44.4),('EN2nnxrg8u',-20.3),('BcHEaaTCvy',88.7),('Tqj8yFmagr',-53.4)],
 'C3zP':[('8avjtjHAHF',28.5),('BcHEaaTCvy',-0.6),('7XLu71Wvq7',198.0),('DrESFkkBLL',13.8),('JCKwsT8UAb',8.3),('7QXDpKoeEe',10.8),('HtTYHz1Kf3',4.3),('DY2ZAaZrt2',-45.4),('Cy1mCA7qAe',6.4),('3Dkwrjfmt3',3.7),('VtwGKv7dcp',9.6),('33eum82LaA',61.8)],
 'B1zhrW':[('FxHzvtCGet',-26.6),('CNcnXNmSSY',14.6),('FJoKnnnDy6',-37.8),('7H8PXXnCSr',2.4),('HekScrY6ts',-9.7),('gRHJb318Qo',-13.7),('JD5sqUJ69N',28.5),('7LmVQxr8UU',13.5),('9TdYvCWsT2',-6.1),('E1S2RV6aNS',-8.2),('Gp7Ke62udV',51.8),('CHBPw8NYTR',-6.9)],
 'Zsp75':[('9smMJxtru3',-15.8),('5Wy5M14iHC',-19.4),('4XuBH9bngH',-16.1),('D5MZMfvPvh',22.1),('BcHEaaTCvy',-8.3),('yoA2CoHk6H',52.7),('AKUYQxitb6',27.5),('EeSHyt1ahS',-31.4),('7bK4jRMa3a',0.5),('FUkQZqqYSx',11.1),('E2oecxpiXH',-13.7),('7XLu71Wvq7',6.8)],
 'jStURX':[('DY2ZAaZrt2',-51.7),('9smMJxtru3',28.1),('6RXiM7kFbV',-29.1),('CcZShPVDms',-34.3),('3x6apzqJKg',-5.8),('6TPQEMKviA',-2.0),('EeSHyt1ahS',436989.4),('BcHEaaTCvy',5.4),('Tqj8yFmagr',-90.9),('CARDSccUMF',9834860.9),('q2yC2PPhZq',42.3),('8fc2o2vPgX',9.2)],
 '7d54Pt':[('4MQBR5zmSJ',18451.3),('Yv7eLDZxGs',68.2),('24FFS5emD3',18441.9),('5cMcYeG4PS',47544.2),('DY2ZAaZrt2',164.7),('HxTwxWrwJ8',53.0),('5K2mmc16PD',-48.1),('5rka6RgVX3',184.9)],
 'ArWird':[('SV151D5pjy',14089.1),('7QpiDt12Wk',162261.3),('4EjSzThNDV',15.1),('JCKwsT8UAb',12.9),('VtwGKv7dcp',9.2),('HdeAPoHivs',23.3),('33eum82LaA',-4.8),('J4x1EMmQjF',-22.4)],
 'DaxfeJ':[('yoA2CoHk6H',-8.8),('2dJniDEAGC',1.3),('CHBPw8NYTR',-96.1),('E2oecxpiXH',-5.6),('4XuBH9bngH',1.5),('6d9PCh5ocA',-4.5),('3WjLscH2Js',-30.8),('7YMkZZwdcw',-6.9),('6yV9ukrWJc',7.5),('pnYrxqat1m',0.6),('Cy1mCA7qAe',-3.7),('DrESFkkBLL',6.3)],
 'DznHqB':[('3ne9QxYRHy',-26.6),('9ZtbETDNjn',78.3),('BcHEaaTCvy',27.8),('DY2ZAaZrt2',25.8),('CeL9aMU8Dm',-58.5),('139jgJYu2N',0.7)],
 '2tYcX':[('HqhumkTH3Y',21.7),('J3nhtXh9A8',21.4),('3LrPMunyVp',23.6),('VANT7vBTHv',-14.0),('AKKAPZBnJn',80.5),('GtC43xtkGe',25.1),('iuv59R3W45',141.4),('FKshTXX4wU',11.6),('8xt6zzGFYf',109.9),('5c4HyD2rSS',2.4),('E5WjTzpuUn',25.3),('EhkrQGCnGf',2.1)],
}

# Build our closed trades aggregated by token10
from collections import defaultdict
our = defaultdict(list)  # token10 -> list of pnl_pct (closed only)
our_full = defaultdict(list)
for r in d:
    if r.get('fully_closed') and r.get('pnl_pct') is not None and r.get('type')=='sell':
        a=r.get('address')
        if a:
            our[a[:10]].append(r['pnl_pct'])
# also try without type filter
our2 = defaultdict(list)
for r in d:
    if r.get('fully_closed') and r.get('pnl_pct') is not None:
        a=r.get('address')
        if a:
            our2[a[:10]].append((r['pnl_pct'], r.get('bot_id'), r.get('hold_secs'), r.get('peak_pnl_pct'), r.get('type'), r.get('kind')))
print('distinct token10 in our closed (sell):', len(our))
print('distinct token10 in our closed (all):', len(our2))
print()

# Head to head
rows=[]
seen_tokens={}
for w, trips in winners.items():
    for tok,ret in trips:
        ours = our2.get(tok)
        if ours:
            pcts=[x[0] for x in ours]
            holds=[x[2] for x in ours if x[2] is not None]
            peaks=[x[3] for x in ours if x[3] is not None]
            rows.append((w,tok,ret,statistics.median(pcts),statistics.mean(pcts),len(pcts),
                         statistics.median(holds)/60 if holds else None,
                         statistics.median(peaks) if peaks else None))
print(f'{\"wallet\":8} {\"token\":11} {\"their_ret%\":>10} {\"our_med%\":>9} {\"our_mean%\":>9} {\"n\":>3} {\"our_holdmin\":>11} {\"our_peak%\":>9}')
for r in sorted(rows,key=lambda x:x[0]):
    hold = f'{r[6]:.1f}' if r[6] is not None else 'NA'
    peak = f'{r[7]:.1f}' if r[7] is not None else 'NA'
    print(f'{r[0]:8} {r[1]:11} {r[2]:10.1f} {r[3]:9.2f} {r[4]:9.2f} {r[5]:3d} {hold:>11} {peak:>9}')
print()
print('matched trips:',len(rows))
