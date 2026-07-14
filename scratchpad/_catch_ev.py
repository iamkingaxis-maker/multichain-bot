import json, statistics as st
from collections import defaultdict
rows=json.load(open('_catch_rows.json'))
rows=[r for r in rows if not (r['pnl'] is not None and r['pnl']>0 and r['hold'] is not None and r['hold']<10)]
rows=[r for r in rows if r['pnl'] is not None and r['liq'] is not None and r['pc_h6'] is not None]
# collapse to entry EVENTS: (addr, minute) across bots -> median pnl, max peak, min mae
ev=defaultdict(list)
for r in rows:
    ev[(r['addr'], r['time'][:16])].append(r)
events=[]
for k,g in ev.items():
    pnls=[x['pnl'] for x in g]
    peaks=[x['peak'] for x in g if x['peak'] is not None]
    maes=[x['mae'] for x in g if x['mae'] is not None]
    e=dict(addr=k[0],sym=g[0]['sym'],time=k[0] and g[0]['time'],
        pnl=st.median(pnls), peak=max(peaks) if peaks else None, mae=min(maes) if maes else None,
        liq=g[0]['liq'],pc_h6=g[0]['pc_h6'],pc_h24=g[0]['pc_h24'],pc_h1=g[0]['pc_h1'],
        age=g[0]['age'],medbuy=g[0]['medbuy'],ubuyers=g[0]['ubuyers'],nbot=len(g))
    events.append(e)
print('ENTRY EVENTS',len(events),'distinct pairs',len(set(e['addr'] for e in events)))
json.dump(events,open('_catch_events.json','w'))

def stats(sub):
    n=len(sub)
    if not n: return None
    pnls=[e['pnl'] for e in sub]
    peaks=[e['peak'] for e in sub if e['peak'] is not None]
    held=sum(1 for e in sub if e['peak'] is not None and e['peak']>=8)/n     # bounce delivered +8
    deadcat=sum(1 for e in sub if (e['peak'] is None or e['peak']<3) and e['pnl']<=-10)/n
    gap=sum(1 for p in pnls if p<=-15)/n
    return dict(n=n,pairs=len(set(e['addr'] for e in sub)),held=round(held,3),dead=round(deadcat,3),gap=round(gap,3),medpnl=round(st.median(pnls),2))
print('BASE(events)',stats(events))
def rep(name,key,edges):
    print('===',name,'===')
    for i in range(len(edges)+1):
        lo=edges[i-1] if i>0 else None; hi=edges[i] if i<len(edges) else None
        sub=[e for e in events if e.get(key) is not None and (lo is None or e[key]>=lo) and (hi is None or e[key]<hi)]
        s=stats(sub)
        if s: print('  [%s,%s): n=%3d pairs=%3d held=%.3f dead=%.3f gap=%.3f medpnl=%s'%(lo,hi,s['n'],s['pairs'],s['held'],s['dead'],s['gap'],s['medpnl']))
rep('LIQ',    'liq',   [30000,50000,80000])
rep('PC_H6',  'pc_h6', [0,50,100,300])
rep('PC_H24', 'pc_h24',[0,150,400,900])
rep('AGE',    'age',   [3,12,48])
rep('MEDBUY', 'medbuy',[15,40])
rep('UBUYERS','ubuyers',[20,50])
