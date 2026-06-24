import statistics, pickle
results,recs=pickle.load(open('_cd_results.pkl','rb'))
n=len(recs)
overall=statistics.mean([pp for f,pp in recs])
print('overall mean pnl_pct %.3f  n=%d' % (overall,n))

def rescue(key, op, thr):
    kept=[(f,pp) for f,pp in recs if key in f and f[key] is not None and (f[key]>=thr if op=='>=' else f[key]<=thr)]
    if not kept: return None
    mean=statistics.mean([pp for f,pp in kept])
    cov=len(kept)/n
    wr=100*sum(1 for f,pp in kept if pp>0)/len(kept)
    return mean,cov*100,wr,len(kept)

# candidates: (feature, op, threshold-between-medians)
cands=[
 ('chart_trendline_5m_breakdown','>=',1.0),      # wmed1 lmed0 binary -> requires breakdown==1
 ('chart_vp_above_poc','<=',0.0),                # wmed0 lmed1 -> below poc
 ('chart_sr_5m_below_broken','>=',1.0),
 ('1m_green_in_last3','>=',2.0),
 ('5m_consec_green','<=',0.0),
 ('net_flow_15s_imbalance','>=',0.37),
 ('net_flow_15s_imbalance','>=',0.40),
 ('buy_pressure_60s','>=',0.60),
 ('time_since_local_low_s','<=',450.0),
 ('trend_30m_r_squared','>=',0.42),
 ('5m_lower_highs','>=',7.0),
 ('dip_leg_candles','>=',1.0),
 ('bb_pos_15m','<=',0.45),
 ('pct_above_vwap_1h','<=',-4.0),
 ('rt_buys_n','>=',19.0),
 ('unique_buyers_n','>=',15.0),
 ('net_flow_60s_n','>=',11.0),
]
print('\n%-40s %4s %7s | %9s %5s %5s %6s' % ('feature','op','thr','keptMean','cov%','wr%','nKept'))
out=[]
for k,op,thr in cands:
    r=rescue(k,op,thr)
    if r is None: print(k,'no data'); continue
    mean,cov,wr,nk=r
    flag='*RESCUE*' if mean>0 else ''
    print('%-40s %4s %7.2f | %9.3f %5.0f %5.0f %6d %s' % (k,op,thr,mean,cov,wr,nk,flag))
    out.append((mean,k,op,thr,cov,wr,nk))
