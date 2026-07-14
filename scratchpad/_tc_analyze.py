import json, statistics as st, math
P=json.load(open('scratchpad/_tc_positions.json'))
n=len(P)
def wr(rows): 
    v=[p['pnl'] for p in rows]; return 100*sum(1 for x in v if x>0)/len(v) if v else float('nan')
def gap(rows,thr=-12):
    v=[p['pnl'] for p in rows]; return 100*sum(1 for x in v if x<thr)/len(v) if v else float('nan')
def med(rows): return st.median([p['pnl'] for p in rows]) if rows else float('nan')
def p05(rows):
    v=sorted(p['pnl'] for p in rows); return v[max(0,int(0.05*len(v))-0)] if v else float('nan')
BASE_WR=wr(P); BASE_GAP=gap(P); BASE_MED=med(P)
print('BASE n=%d WR=%.1f gap%%=%.1f med=%.2f  (gap=pnl<-12)'%(n,BASE_WR,BASE_GAP,BASE_MED))
print('gap-thru thresholds: <-12: %.1f%%  <-15: %.1f%%  <-20: %.1f%%'%(gap(P,-12),gap(P,-15),gap(P,-20)))
print()
def getf(p,f): 
    v=p['em'].get(f); 
    return v if isinstance(v,(int,float)) else None
def sweep(f,label):
    rows=[p for p in P if getf(p,f) is not None]
    if len(rows)<40: 
        print('  [skip %s n=%d]'%(label,len(rows))); return
    vals=sorted(getf(p,f) for p in rows)
    lo=vals[len(vals)//3]; hi=vals[2*len(vals)//3]
    L=[p for p in rows if getf(p,f)<=lo]
    H=[p for p in rows if getf(p,f)>=hi]
    print('%-26s n=%d  T1(<=%.4g) WR=%.0f gap=%.0f med=%.1f | T3(>=%.4g) WR=%.0f gap=%.0f med=%.1f  dWR=%+.0f dGAP=%+.0f'%(
        label,len(rows),lo,wr(L),gap(L),med(L),hi,wr(H),gap(H),med(H),wr(H)-wr(L),gap(H)-gap(L)))
print('=== SWING / VOLATILITY (higher = more violent) ===')
for f,l in [('token_volatility_h24_pct','tok_vol_h24'),('shape_30m_range_pct','shape30m_range'),
    ('1m_range_pct_last','1m_range_last'),('1s_range_pct_60s','1s_range_60s'),('1m_body_pct_avg','1m_body_avg'),
    ('shape_30m_drawdown_from_max_pct','shape30m_ddown'),('chart_entry_range_pct','entry_range')]:
    sweep(f,l)
print('=== HOLDER CONCENTRATION ===')
for f,l in [('top10_holder_pct','top10_pct'),('top1_holder_pct','top1_pct'),('top1_share_of_top10','top1_of_top10'),('topholder_insider_n','insider_n')]:
    sweep(f,l)
print('=== LIQUIDITY / DRAIN ===')
for f,l in [('liquidity_usd','liq_usd'),('lp_delta_5m_pct','lp_d5m'),('lp_delta_15m_pct','lp_d15m'),('liq_velocity_m5_usd_per_txn','liqvel_m5')]:
    sweep(f,l)
print('=== PUMP STATE ===')
for f,l in [('pc_h6','pc_h6'),('pc_h24','pc_h24'),('pc_h1','pc_h1'),('pc_m5','pc_m5')]:
    sweep(f,l)
print('=== BUYER QUALITY ===')
for f,l in [('median_buy_size_usd','med_buy_usd'),('unique_buyers_n','uniq_buyers'),
    ('n_recurring_buyers_3plus','recur_buyers'),('unique_buyer_ratio','uniq_ratio'),('mean_buy_size_usd','mean_buy')]:
    sweep(f,l)
print('=== ENTRY LOCATION (the DONALD "over the low" axis) ===')
for f,l in [('pct_in_5m_range','pct_in_5m_rng'),('pct_in_1h_range','pct_in_1h_rng'),
    ('hl_delta_pct','hl_delta'),('time_since_local_low_s','since_low_s'),('pct_off_peak','pct_off_peak')]:
    sweep(f,l)
