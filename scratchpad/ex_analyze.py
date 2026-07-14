import json, statistics as st
from collections import defaultdict
E=json.load(open('_ex_episodes.json'))
def num(x):
    try:
        if x is None or isinstance(x,bool): return None
        return float(x)
    except: return None

def summ(rows):
    if not rows: return (0,None,None)
    wr=sum(r['win'] for r in rows)/len(rows)
    return (len(rows),wr,st.median([r['med_pnl'] for r in rows]))

print('BASE n=%d WR=%.3f medpnl=%.2f'%summ(E))
print()

# continuous features: split by median of non-null
def analyze_cont(feat,invert=False):
    vals=[(num(r.get(feat)),r) for r in E]
    vals=[(v,r) for v,r in vals if v is not None]
    if len(vals)<40:
        print('%-32s n=%d too few'%(feat,len(vals))); return
    xs=sorted(v for v,_ in vals)
    med=xs[len(xs)//2]
    lo=[r for v,r in vals if v<=med]; hi=[r for v,r in vals if v>med]
    nl,wl,pl=summ(lo); nh,wh,ph=summ(hi)
    # per-pair robustness: among pairs with >=4 eps having both sides
    bypair=defaultdict(lambda:{'lo':[],'hi':[]})
    for v,r in vals:
        bypair[r['addr']]['lo' if v<=med else 'hi'].append(r['win'])
    rob=0;tot=0
    for a,d in bypair.items():
        if len(d['lo'])>=2 and len(d['hi'])>=2:
            tot+=1
            if (sum(d['hi'])/len(d['hi']))>(sum(d['lo'])/len(d['lo'])): rob+=1
    print('%-30s med=%7.3f | LO n=%3d WR=%.2f pnl=%+5.1f | HI n=%3d WR=%.2f pnl=%+5.1f | dWR=%+.2f | pairrob %d/%d'%(
        feat,med,nl,wl,pl,nh,wh,ph,wh-wl,rob,tot))

def analyze_bool(feat):
    t=[r for r in E if r.get(feat) is True]; f=[r for r in E if r.get(feat) is False]
    nt,wt,pt=summ(t); nf,wf,pf=summ(f)
    print('%-30s | TRUE n=%3d WR=%.2f pnl=%+5.1f | FALSE n=%3d WR=%.2f pnl=%+5.1f'%(feat,nt,wt or 0,pt or 0,nf,wf or 0,pf or 0))

def analyze_cat(feat):
    g=defaultdict(list)
    for r in E: g[r.get(feat)].append(r)
    for k,rows in sorted(g.items(),key=lambda kv:-len(kv[1])):
        n,w,p=summ(rows); print('%-30s = %-10s n=%3d WR=%.2f pnl=%+5.1f'%(feat,str(k),n,w,p))

print('== EXHAUSTION FAMILY (continuous, HI vs LO on median) ==')
for f in ['sell_volume_decay_ratio_30s','1s_vol_decay_120s','1s_bars_since_low_60s','1s_bottom_score',
          '1s_lower_wick_ratio_last','1s_close_pos_60s','1s_red_count_60s','1s_vol_burst_on_reversal_ratio',
          '1s_cascade_reversal_pct','largest_buy_to_largest_sell','buy_sell_volume_imbalance',
          'net_flow_15s_imbalance','net_flow_60s_imbalance','net_flow_5m_imbalance',
          'net_flow_15s_usd','net_flow_60s_usd','net_flow_5m_usd','rt_max_sell_usd','rt_consec_sells','sell_burst_30s_count']:
    analyze_cont(f)
print()
print('== DEMAND FAMILY ==')
for f in ['buy_pressure_60s','buy_burst_30s_count','median_buy_size_usd','unique_buyers_n']:
    analyze_cont(f)
print()
print('== BOOLEANS ==')
for f in ['1s_sweep_reject_detected','1s_cascade_reversal_detected','1s_base_confirmed_at_entry']:
    analyze_bool(f)
print()
print('== hl_confirm_state ==')
analyze_cat('hl_confirm_state')
print()
print('== context ==')
for f in ['pc_h1','pc_h6','pc_h24','liquidity_usd','1s_bars_60s']:
    analyze_cont(f)
