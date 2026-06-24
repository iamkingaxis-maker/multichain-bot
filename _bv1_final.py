import json
d=json.load(open('_bv1_trades.json'))
sells=[t for t in d if t.get('bot_id')=='baseline_v1' and t.get('type')=='sell']
recs=[]
for s in sells:
    p=s.get('pnl_pct'); em=s.get('entry_meta')
    if p is None or abs(p)>300 or not isinstance(em,dict) or not em: continue
    recs.append((p,em))
N=len(recs)
def nv(v): return float(v) if isinstance(v,(int,float,bool)) else None

# robust top candidate: mean_buy_size_usd (cov 30%). winners small buys.
# Two missing-handling interpretations
for feat,op,thr in [('mean_buy_size_usd','<=',50),('rt_avg_buy_usd','<=',60),('minutes_since_peak','>=',50)]:
    present=[(p,nv(em.get(feat))) for p,em in recs if nv(em.get(feat)) is not None]
    cov=len(present)
    win=[p for p,em in recs if p>0]
    los=[p for p,em in recs if p<=0]
    import statistics
    wv=[nv(em.get(feat)) for p,em in recs if p>0 and nv(em.get(feat)) is not None]
    lv=[nv(em.get(feat)) for p,em in recs if p<=0 and nv(em.get(feat)) is not None]
    print('\n%s %s %g  (cov %d/%d=%.0f%%)'%(feat,op,thr,cov,N,cov/N*100))
    print('  winner_med=%.3f loser_med=%.3f'%(statistics.median(wv),statistics.median(lv)))
    kept=[p for p,v in present if (op=='<=' and v<=thr) or (op=='>=' and v>=thr)]
    km=sum(kept)/len(kept) if kept else 0
    print('  KEPT-subset (present&pass): n=%d mean=%+.3f wr=%.1f%%'%(len(kept),km,100*sum(1 for x in kept if x>0)/len(kept)))
