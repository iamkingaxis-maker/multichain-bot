import json
from collections import defaultdict
d=json.load(open(r'C:\Users\jcole\multichain-bot\_wsig_full.json'))
t=d if isinstance(d,list) else d['trades']
gp=[x for x in t if x.get('bot_id')=='pool_a_goodpond']
buys=[x for x in gp if x['type']=='buy']; sells=[x for x in gp if x['type']=='sell']
sba=defaultdict(list)
for s in sells: sba[s['address']].append(s)
for a in sba: sba[a].sort(key=lambda x:x['time'])
rows=[]
for b in sorted(buys,key=lambda x:x['time']):
    c=[s for s in sba.get(b['address'],[]) if s['time']>b['time']]
    if c: rows.append((b,c[0],float(c[0]['pnl_pct'])))
rows=[r for r in rows if abs(r[2])<=300]
for feat in ['chart_reaccum_drawdown_pct','shape_30m_drawdown_from_max_pct','net_flow_5m_usd','shape_30m_chg_pct']:
    print('==',feat)
    w=sorted((b['entry_meta'].get(feat),round(p,1)) for b,s,p in rows if p>0)
    l=sorted((b['entry_meta'].get(feat),round(p,1)) for b,s,p in rows if p<=0)
    print('  WIN:',[ (round(v,2) if v is not None else None,pp) for v,pp in w])
    print('  LOSE:',[ (round(v,2) if v is not None else None,pp) for v,pp in l])
