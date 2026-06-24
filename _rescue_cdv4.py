import pickle, statistics
pairs,winners,losers=pickle.load(open('_cdv4_pairs.pkl','rb'))

def feat(b,k):
    em=b.get('entry_meta') or {}
    v=em.get(k)
    if isinstance(v,bool): return None
    if isinstance(v,(int,float)): return float(v)
    return None

allp=pairs
overall_mean=statistics.mean([p[2] for p in allp])
print('overall mean pnl_pct', round(overall_mean,3),'n',len(allp))

def test_gate(k, op, thr):
    kept=[]
    dropped=[]
    nonnull=0
    for b,s,p in allp:
        v=feat(b,k)
        if v is None:
            # missing -> dropped (gate can't pass)
            dropped.append(p); continue
        nonnull+=1
        ok = (v>=thr) if op=='>=' else (v<=thr)
        (kept if ok else dropped).append(p)
    if not kept: return None
    km=statistics.mean(kept)
    wk=sum(1 for x in kept if x>0)
    cov=nonnull/len(allp)
    return dict(k=k,op=op,thr=thr,n_kept=len(kept),kept_mean=round(km,3),
                kept_wr=round(100*wk/len(kept),1),cov=round(100*cov,1),
                dropped_mean=round(statistics.mean(dropped),3) if dropped else None,
                n_dropped=len(dropped))

# Candidate clauses (direction from winner vs loser medians; winners higher -> >=, lower -> <=)
cands=[
 ('chart_stop_cluster_5m_density','<=',1),
 ('shape_90m_lh_count','<=',0),
 ('shape_60m_lh_count','<=',0),
 ('trend_60m_consec_lh','<=',0),
 ('pc_h1_change_since_lookback','<=',-10),   # winners more negative (deeper dip)
 ('shape_90m_chg_pct','<=',0),               # winners ~ -6 vs losers +24
 ('trend_60m_r_squared','>=',0.2),
 ('minutes_since_peak','<=',120),
 ('net_flow_15s_imbalance','>=',0.3),
 ('5m_consec_green','<=',0),
 ('1m_max_drop','<=',-4),
 ('token_volatility_h24_pct','<=',85),
 ('buys_h1','<=',700),
 ('bb_pos_5m','<=',0.45),
]
print('\n=== RESCUE / GATE TEST ===')
rows=[]
for k,op,thr in cands:
    r=test_gate(k,op,thr)
    if r: rows.append(r); 
for r in sorted(rows,key=lambda x:-x['kept_mean']):
    print(f"{r['k']:32s} {r['op']}{r['thr']:>6} keptN={r['n_kept']:3d} keptMean={r['kept_mean']:7.3f} keptWR={r['kept_wr']:5.1f}% drop={r['n_dropped']:2d} dropMean={r['dropped_mean']} cov={r['cov']}%")
