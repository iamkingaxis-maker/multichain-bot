import json, statistics
d=json.load(open('_bv1_trades.json'))
sells=[t for t in d if t.get('bot_id')=='baseline_v1' and t.get('type')=='sell']
recs=[]
for s in sells:
    p=s.get('pnl_pct'); em=s.get('entry_meta')
    if p is None or abs(p)>300 or not isinstance(em,dict) or not em: continue
    recs.append((p,em))
N=len(recs)
allmean=sum(r[0] for r in recs)/N
print('N=%d overall_mean=%.3f WR=%.1f%%'%(N,allmean,100*sum(1 for r in recs if r[0]>0)/N))

def numval(v):
    if isinstance(v,bool): return float(v)
    if isinstance(v,(int,float)): return float(v)
    return None

def test_gate(feat, op, thr):
    kept=[]; cov=0
    for p,em in recs:
        v=numval(em.get(feat))
        if v is None: continue
        cov+=1
        if op=='>=' and v>=thr: kept.append(p)
        elif op=='<=' and v<=thr: kept.append(p)
    if not kept: return None
    km=sum(kept)/len(kept)
    kwr=100*sum(1 for x in kept if x>0)/len(kept)
    return (feat,op,thr,len(kept),km,kwr,cov/N*100)

# candidate gates: feature, direction (winners-side), threshold between medians
# pick robust coverage>=26%, not default-0-hardblock, not inverted
cands=[
 ('top10_buyer_time_spread_sec','<=',180),   # winners faster sniped spread (66 vs 436)
 ('rt_time_span_secs','<=',180),
 ('jito_tip_p99_lamports','<=',500000),
 ('shape_30m_drawdown_from_max_pct','<=',-8),  # winners deeper dip (-4 vs -12? winners=-4 higher=shallower) check
 ('net_flow_60s_imbalance','<=',0.25),
 ('net_flow_15s_imbalance','<=',0.5),
 ('mean_buy_size_usd','<=',50),
 ('rt_avg_buy_usd','<=',60),
 ('minutes_since_peak','>=',50),
 ('shape_90m_mins_since_max','>=',40),
 ('1m_close_in_range','>=',0.4),
 ('trend_score_raw','>=',0),
 ('top10_buyer_within_60s_count','>=',5),
 ('lower_wick_ratio_5m','>=',0),
]
print('\nfeat / op thr / n_kept / kept_mean / kept_WR / coverage%')
for c in cands:
    r=test_gate(*c)
    if r: print('%-28s %s %-8g n=%-4d mean=%+.3f wr=%.1f%% cov=%.0f%%'%(r[0],r[1],r[2],r[3],r[4],r[5],r[6]))
