import json, statistics

def load(path):
    d=json.load(open(path))
    return d.get('trades',d) if isinstance(d,dict) else d

fresh=load('./_tf_fresh.json')
cache=load('./_trades_cache.json')

# merge, dedupe by (bot_id,address,type,time)
seen=set(); merged=[]
for src in (fresh,cache):
    for r in src:
        if r.get('bot_id')!='pool_a_dipgate': continue
        k=(r.get('address'),r.get('type'),r.get('time'))
        if k in seen: continue
        seen.add(k); merged.append(r)

buys=[r for r in merged if r.get('type')=='buy']
sells=[r for r in merged if r.get('type')=='sell']
print('merged dipgate buys',len(buys),'sells',len(sells))

# sort sells per address by time
from collections import defaultdict
sells_by_addr=defaultdict(list)
for s in sells:
    sells_by_addr[s.get('address')].append(s)
for a in sells_by_addr: sells_by_addr[a].sort(key=lambda r:r.get('time',''))

# join each buy to temporally-next sell for same address (bot fixed)
pairs=[]
for b in sorted(buys,key=lambda r:r.get('time','')):
    bt=b.get('time',''); a=b.get('address')
    nxt=None
    for s in sells_by_addr.get(a,[]):
        if s.get('time','')>=bt:
            nxt=s; break
    if nxt is None: continue
    pp=nxt.get('pnl_pct')
    if pp is None: continue
    if abs(pp)>300: continue  # phantom guard
    pairs.append((b,nxt,pp))

print('joined pairs (after phantom drop)', len(pairs))
wins=[p for p in pairs if p[2]>0]; losers=[p for p in pairs if p[2]<=0]
print('winners',len(wins),'losers',len(losers))
if pairs:
    pcs=[p[2] for p in pairs]
    print('WR pct', round(100*len(wins)/len(pairs),1))
    print('median pnl_pct', round(statistics.median(pcs),2))
json.dump([[p[2]] for p in pairs], open('./_pairs_pnl.json','w'))

# stash pairs buys for feature mining
json.dump({'buys_win':[p[0].get('entry_meta',{}) for p in wins],
           'buys_lose':[p[0].get('entry_meta',{}) for p in losers],
           'all_buys':[b.get('entry_meta',{}) for b in buys]},
          open('./_dipgate_meta.json','w'))
