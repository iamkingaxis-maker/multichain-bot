import json, statistics, math
d=json.load(open('_bv1_trades.json'))
sells=[t for t in d if t.get('bot_id')=='baseline_v1' and t.get('type')=='sell']

# outcome population: sells with pnl_pct and entry_meta
recs=[]
for s in sells:
    p=s.get('pnl_pct')
    em=s.get('entry_meta')
    if p is None: continue
    if abs(p)>300: continue
    if not isinstance(em,dict) or not em: continue
    recs.append((p,em,s))
print('joined pairs (sells w/ meta, |pnl|<=300):', len(recs))
pnls=[r[0] for r in recs]
print('mean pnl_pct %.3f'%(sum(pnls)/len(pnls)))
print('WR %.1f%%'%(100*sum(1 for p in pnls if p>0)/len(pnls)))

win=[r for r in recs if r[0]>0]
los=[r for r in recs if r[0]<=0]
print('winners',len(win),'losers',len(los))

# numeric features from entry_meta (+ a couple sell-side derived NOT used as gate)
# holder-only = not gate-readable: top10_holder_pct, top1_holder_pct, top1_share_of_top10
holder_only={'top10_holder_pct','top1_holder_pct','top1_share_of_top10'}
def numval(v):
    if isinstance(v,bool): return float(v)
    if isinstance(v,(int,float)): return float(v)
    return None

# collect numeric feature keys
keys=set()
for _,em,_ in recs:
    for k,v in em.items():
        if numval(v) is not None: keys.add(k)

def med(xs): return statistics.median(xs) if xs else float('nan')
results=[]
for k in keys:
    wv=[numval(r[1].get(k)) for r in win if numval(r[1].get(k)) is not None]
    lv=[numval(r[1].get(k)) for r in los if numval(r[1].get(k)) is not None]
    cov=sum(1 for r in recs if numval(r[1].get(k)) is not None)/len(recs)
    if len(wv)<5 or len(lv)<5: continue
    wm,lm=med(wv),med(lv)
    allv=wv+lv
    sd=statistics.pstdev(allv) if len(allv)>1 else 0
    sep=abs(wm-lm)/sd if sd>0 else 0
    results.append((sep,k,wm,lm,cov,sd))

results.sort(reverse=True)
print('\n=== RANKED SEPARATORS ===')
for sep,k,wm,lm,cov,sd in results:
    gr = 'false' if k in holder_only else 'true'
    print('%.3f  %-30s wmed=%.4g lmed=%.4g cov=%.0f%% gr=%s'%(sep,k,wm,lm,cov*100,gr))
