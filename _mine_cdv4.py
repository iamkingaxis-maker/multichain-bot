import json, statistics, math
from datetime import datetime

d=json.load(open('_trades_full.json'))
BOT='champion_defender_v4'

def parse(t): return datetime.fromisoformat(t['time'])

buys=[t for t in d if t.get('bot_id')==BOT and t.get('type')=='buy']
sells=[t for t in d if t.get('bot_id')==BOT and t.get('type')=='sell']

# index sells per address sorted by time
from collections import defaultdict
sells_by_addr=defaultdict(list)
for s in sells:
    sells_by_addr[s['address']].append(s)
for a in sells_by_addr: sells_by_addr[a].sort(key=parse)

pairs=[]
used=set()
for b in sorted(buys,key=parse):
    addr=b['address']; bt=parse(b)
    cand=None
    for s in sells_by_addr.get(addr,[]):
        sid=id(s)
        if sid in used: continue
        if parse(s)>=bt:
            cand=s; break
    if cand is None: continue
    used.add(id(cand))
    pnl=cand.get('pnl_pct')
    if pnl is None: continue
    if abs(pnl)>300: continue
    pairs.append((b,cand,pnl))

print('buys',len(buys),'sells',len(sells),'joined pairs',len(pairs))
pnls=[p[2] for p in pairs]
if pnls:
    print('mean pnl_pct', round(statistics.mean(pnls),3))
    print('median', round(statistics.median(pnls),3))
    wins=[x for x in pnls if x>0]
    print('WR', round(100*len(wins)/len(pnls),1), 'n_win',len(wins),'n_loss',len(pnls)-len(wins))

# dump entry_meta numeric feature analysis
winners=[p for p in pairs if p[2]>0]
losers=[p for p in pairs if p[2]<=0]
print('winners',len(winners),'losers',len(losers))

# collect all numeric feature keys from entry_meta
def feats(b):
    em=b.get('entry_meta') or {}
    out={}
    for k,v in em.items():
        if isinstance(v,bool): continue
        if isinstance(v,(int,float)) and not isinstance(v,bool):
            out[k]=float(v)
    return out

allkeys=set()
for b,s,p in pairs:
    allkeys|=set(feats(b).keys())

# holder-only markers
def readable(k):
    kl=k.lower()
    if kl.startswith('holder') or 'top10_holder' in kl or k.endswith('_hf') or '_hf_' in kl or 'holder_' in kl:
        return 'false'
    return 'true'

rows=[]
for k in allkeys:
    wv=[feats(b).get(k) for b,s,p in winners if feats(b).get(k) is not None]
    lv=[feats(b).get(k) for b,s,p in losers if feats(b).get(k) is not None]
    cov=sum(1 for b,s,p in pairs if feats(b).get(k) is not None)/len(pairs)
    if len(wv)<3 or len(lv)<3: continue
    wm=statistics.median(wv); lm=statistics.median(lv)
    allv=wv+lv
    sd=statistics.pstdev(allv) if len(allv)>1 else 0
    sep=abs(wm-lm)/sd if sd>0 else 0
    rows.append((k,wm,lm,sep,cov,len(wv),len(lv)))

rows.sort(key=lambda r:-r[3])
print('\n=== TOP SEPARATORS ===')
for k,wm,lm,sep,cov,nw,nl in rows[:35]:
    print(f'{k:42s} wmed={wm:12.5g} lmed={lm:12.5g} sep={sep:6.3f} cov={cov*100:5.1f}% nw={nw} nl={nl} {"READ" if readable(k)=="true" else "HOLDER"}')

import pickle
pickle.dump((pairs,winners,losers),open('_cdv4_pairs.pkl','wb'))
