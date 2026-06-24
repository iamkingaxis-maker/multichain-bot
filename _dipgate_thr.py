import json, statistics, math
d=json.load(open('./_dipgate_meta.json'))
win=d['buys_win']; lose=d['buys_lose']

def get(metas,k):
    out=[]
    for m in metas:
        v=m.get(k)
        if isinstance(v,bool): continue
        if isinstance(v,(int,float)) and not (isinstance(v,float) and math.isnan(v)):
            out.append(float(v))
    return out

# candidates: feature, direction ('>=' winners higher OR '<=' winners lower), threshold
cands = [
 ('shape_30m_drawdown_from_max_pct','<=',-12.0),
 ('shape_30m_chg_pct','<=',-6.0),
 ('macro30_pct','<=',-6.0),
 ('pct_above_vwap_1h','<=',-4.0),
 ('trend_ma50_dist_pct','<=',-4.0),
 ('trend_ma20_dist_pct','<=',-1.5),
 ('net_flow_5m_imbalance','<=',0.05),
 ('rt_dollar_imbalance','<=',0.05),
 ('1m_higher_highs','<=',1.0),
 ('1m_green_in_last3','<=',1.0),
 ('trend_30m_slope_pct_per_min','<=',-0.15),
 ('shape_30m_max_over_entry_pct','>=',14.0),
 ('trend_30m_r_squared','>=',0.30),
]
for k,direction,thr in cands:
    wv=get(win,k); lv=get(lose,k)
    if direction=='<=':
        wk=sum(1 for v in wv if v<=thr); lk=sum(1 for v in lv if v<=thr)
    else:
        wk=sum(1 for v in wv if v>=thr); lk=sum(1 for v in lv if v>=thr)
    # winners kept among those with data, losers blocked
    wret=100*wk/len(wv) if wv else 0
    lblk=100*(len(lv)-lk)/len(lv) if lv else 0
    # post-gate WR among joined pairs that have data for k
    passed_w=wk; passed_l=lk
    postwr=100*passed_w/(passed_w+passed_l) if (passed_w+passed_l)>0 else 0
    print("%-34s %s %7.3g | winners kept %4.0f%% (%d/%d) | losers blocked %4.0f%% (%d/%d) | postgate WR %4.0f%% (n=%d)"%(
        k,direction,thr,wret,wk,len(wv),lblk,len(lv)-lk,len(lv),postwr,passed_w+passed_l))
