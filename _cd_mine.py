import json, statistics, math

BOT='champion_defender_2k'
files=['_trades_cache.json','_all.json','_all2.json','_adv_tr.json','_cd_full.json']
seen=set()
rows=[]
for f in files:
    try:
        d=json.load(open(f))
    except Exception as e:
        print('skip',f,e); continue
    t = d if isinstance(d,list) else d.get('trades',[])
    for x in t:
        if x.get('bot_id')!=BOT: continue
        key=(x.get('type'),x.get('address'),x.get('time'))
        if key in seen: continue
        seen.add(key)
        rows.append(x)
print('merged cd2k rows', len(rows))
buys=[x for x in rows if x.get('type')=='buy']
sells=[x for x in rows if x.get('type')=='sell']
print('buys',len(buys),'sells',len(sells))
times=sorted(x['time'] for x in rows if x.get('time'))
print('range',times[0][:19],times[-1][:19])

# Join: for each buy, find earliest sell for same address with time > buy time
from collections import defaultdict
sells_by_addr=defaultdict(list)
for s in sells:
    sells_by_addr[s.get('address')].append(s)
for a in sells_by_addr: sells_by_addr[a].sort(key=lambda x:x.get('time') or '')

pairs=[]
for b in sorted(buys,key=lambda x:x.get('time') or ''):
    a=b.get('address'); bt=b.get('time')
    cand=[s for s in sells_by_addr.get(a,[]) if (s.get('time') or '')>(bt or '')]
    # use the sell with fully_closed if present else earliest
    if not cand: continue
    # prefer fully_closed True, earliest
    closed=[s for s in cand if s.get('fully_closed')]
    s = (closed[0] if closed else cand[0])
    pp=s.get('pnl_pct')
    if pp is None: continue
    pairs.append((b,s,pp))
print('joined pairs (pre-filter)', len(pairs))
# drop phantom |pnl_pct|>300
pp_all=[p[2] for p in pairs]
pairs=[p for p in pairs if abs(p[2])<=300]
print('pairs after |pnl_pct|<=300', len(pairs))
import pickle
pickle.dump(pairs, open('_cd_pairs.pkl','wb'))
wins=[p for p in pairs if p[2]>0]
losers=[p for p in pairs if p[2]<=0]
print('winners',len(wins),'losers',len(losers))
allpp=[p[2] for p in pairs]
print('WR %.1f' % (100*len(wins)/len(pairs)))
print('mean pnl_pct %.3f' % statistics.mean(allpp))
print('median %.3f' % statistics.median(allpp))
