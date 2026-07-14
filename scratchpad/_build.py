import json, statistics as st
from collections import defaultdict

d=json.load(open('_trades_cache.json'))
d.sort(key=lambda t: t.get('time',''))
ob=defaultdict(list)
trips=[]
for t in d:
    bot,tok,ty=t.get('bot_id'),t.get('token'),(t.get('type') or '').lower()
    if not bot or not tok or not bot.startswith('badday_'): continue
    k=(bot,tok)
    if ty=='buy':
        ob[k].append({'buy':t,'wret':0.0,'fracsum':0.0,'rem':1.0,'peak':None,'mae':None,'hold':None,'lastpnl':None})
    elif ty=='sell' and ob[k]:
        x=ob[k][0]
        fr=t.get('sell_fraction')
        fr=float(fr) if fr is not None else x['rem']
        pp=t.get('pnl_pct')
        if pp is not None:
            x['wret']+=fr*float(pp); x['fracsum']+=fr
        x['rem']-=fr
        pk=t.get('peak_pnl_pct')
        if pk is not None and (x['peak'] is None or float(pk)>x['peak']): x['peak']=float(pk)
        m=t.get('mae_pct')
        if m is not None and (x['mae'] is None or float(m)<x['mae']): x['mae']=float(m)
        x['hold']=t.get('hold_secs'); x['lastpnl']=t.get('pnl_pct')
        if t.get('fully_closed') or x['rem']<=0.01:
            b=x['buy']; em=b.get('entry_meta') if isinstance(b.get('entry_meta'),dict) else {}
            ret=(x['wret']/x['fracsum']) if x['fracsum']>0 else (x['lastpnl'] or 0.0)
            trips.append({'bot':bot,'tok':tok,'t':b.get('time','') or '','ret':ret,
                'peak':x['peak'],'mae':x['mae'],'hold':x['hold'],'em':em})
            ob[k].pop(0)

print('total badday trips:',len(trips))
recent=[c for c in trips if c['t'][:10]>='2026-07-03']
print('recent trips:',len(recent))
# scrub
scr=[c for c in recent if not (c['ret']>0 and (c['hold'] or 1e9)<10)]
print('after scrub:',len(scr),'dropped',len(recent)-len(scr))
young=[c for c in scr if c['bot'].startswith('badday_young_')]
print('young-lane trips:',len(young))

def extop2(rows):
    by=defaultdict(list)
    for c in rows: by[c['tok']].append(c['ret'])
    tm=[st.median(v) for v in by.values()]
    if len(tm)<3: return None,len(tm),None
    tm.sort()
    kept=tm[:-2]
    med=st.median(kept)
    grn=100.0*sum(1 for x in kept if x>0)/len(kept)
    return med,len(tm),grn

for name,rows in [('ALL badday',scr),('young-lane',young)]:
    med,nt,grn=extop2(rows)
    print(f'{name}: ex-top2 median={med:.2f}% n_tok={nt} pct_green={grn:.0f}% n_trip={len(rows)}')

import pickle
pickle.dump(scr,open('scratchpad/_trips.pkl','wb'))
print('saved')
