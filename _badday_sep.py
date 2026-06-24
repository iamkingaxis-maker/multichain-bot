import json,statistics
from collections import defaultdict
recs=json.load(open('_badday_recs.json'))
# candidate decision-time features (numeric, decision-time only)
FEATS=['sol_pc_h1','sol_pc_h6','sol_pc_h24','sol_pc_m5','btc_pc_h1','btc_pc_h4',
 'pc_h1','pc_h6','pc_h24','pc_m5','liquidity_usd','mcap','lifecycle_age_hours','hours_since_graduation',
 'net_flow_15s_imbalance','net_flow_60s_imbalance','net_flow_5m_imbalance','net_flow_60s_usd','net_flow_5m_usd',
 'unique_buyers_n','n_recurring_buyers_3plus','vol_h1','bs_m5','bs_h1','bs_h6',
 'buy_pressure_60s','buy_sell_volume_imbalance','rt_dollar_imbalance','rt_recent_skew',
 'regime_dip_breadth_pct','regime_h1_neg_pct','meme_sector_pct_h24',
 'rsi_5m','rsi_15m','rugcheck_score','top10_holder_pct','top1_holder_pct',
 'unique_buyer_ratio','trades_per_sec_last60s','vol_5m_burst_vs_h1','smart_wallet_count_60s',
 'pct_off_peak','minutes_since_peak','token_volatility_h24_pct','entry_volume_h24_usd',
 'trend_score_norm','chart_mtf_score','chart_score','mean_buy_size_usd','median_buy_size_usd',
 'lp_locked_pct','dip_volume_ratio','time_since_local_low_s','1s_bottom_score',
 'shape_60m_drawdown_from_max_pct','shape_90m_drawdown_from_max_pct','net_flow_15s_n','net_flow_60s_n']

def getf(r,f):
    v=r['em'].get(f)
    return v if isinstance(v,(int,float)) and not isinstance(v,bool) else None

days=['2026-06-21','2026-06-22','2026-06-23']
out=[]
for f in FEATS:
    vals=[(getf(r,f),r) for r in recs]
    vals=[(v,r) for v,r in vals if v is not None]
    if len(vals)<40: continue
    xs=[v for v,_ in vals]
    med=statistics.median(xs)
    # split at median, compare WR + mean_real
    lo=[r for v,r in vals if v<=med]; hi=[r for v,r in vals if v>med]
    if len(lo)<15 or len(hi)<15: continue
    def stats(g): return (sum(x['win'] for x in g)/len(g), statistics.mean(x['real'] for x in g), len(g))
    lwr,lm,ln=stats(lo); hwr,hm,hn=stats(hi)
    gap=hm-lm
    # per-day directional consistency: does the "better" side win each day?
    better='hi' if hm>lm else 'lo'
    perday=[]
    consistent=0
    for dd in days:
        dl=[r for v,r in vals if v<=med and r['day']==dd]
        dh=[r for v,r in vals if v>med and r['day']==dd]
        if len(dl)>=4 and len(dh)>=4:
            dlm=statistics.mean(x['real'] for x in dl); dhm=statistics.mean(x['real'] for x in dh)
            sign = (dhm>dlm)
            ok = (sign and better=='hi') or ((not sign) and better=='lo')
            consistent+= 1 if ok else 0
            perday.append((dd,round(dhm-dlm,1),ok))
        else:
            perday.append((dd,None,None))
    out.append((abs(gap),f,med,(lwr,lm,ln),(hwr,hm,hn),better,consistent,perday))

out.sort(reverse=True)
for ag,f,med,L,H,better,cons,pd in out[:25]:
    print(f"\n{f}  median={med:.4g}  |gap|={ag:.2f}pp  consistent_days={cons}/3  better={better}")
    print(f"   LO(<=med) n={L[2]} WR={L[0]:.2f} real={L[1]:.2f}   HI(>med) n={H[2]} WR={H[0]:.2f} real={H[1]:.2f}")
    print(f"   perday(hi-lo real): {pd}")
