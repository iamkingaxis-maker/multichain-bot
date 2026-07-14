import json
from collections import defaultdict, Counter
buys=[json.loads(l) for l in open('scratchpad/_overgating_buys.jsonl')]
sells=[json.loads(l) for l in open('scratchpad/_overgating_sells.jsonl')]
def key(b): return (b['bot'], b['addr'])
sidx=defaultdict(list)
for s in sells: sidx[key(s)].append(s)
pos=[]; unmatched=0
for b in buys:
    k=key(b); ep=b['ep']
    legs=[s for s in sidx.get(k,[]) if s['ep'] and ep and abs(s['ep']-ep)/ep < 1e-6]
    if not legs:
        unmatched+=1; continue
    cost=0.0; pnl=0.0; ok=False
    for s in legs:
        if s['pnl'] is None or s['pnl_pct'] is None: continue
        if s['pnl_pct']==0:  # zero-return leg, cost unknown; approximate cost as |pnl| small -> skip weight
            continue
        c=s['pnl']/(s['pnl_pct']/100.0)
        if c<=0: continue
        cost+=c; pnl+=s['pnl']; ok=True
    ppct=100*pnl/cost if (ok and cost>0) else None
    peak=max((s['peak'] for s in legs if s['peak'] is not None), default=None)
    hold=max((s['hold'] for s in legs if s['hold'] is not None), default=None)
    pos.append({'bot':b['bot'],'addr':b['addr'],'token':b['token'],'time':b['time'],
                'ppct':ppct,'peak':peak,'hold':hold,
                'kv':b['kv'],'rb':b['rb'],'h24r':b['h24r']})
print('positions:',len(pos),'unmatched:',unmatched,'ppct None:',sum(1 for p in pos if p['ppct'] is None))
json.dump(pos, open('scratchpad/_overgating_pos.json','w'))
