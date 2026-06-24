import json, statistics as st
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
buys={(bot(x),(x.get('address')or x.get('token')or'').lower()):x for x in t if isinstance(x,dict) and x.get('type')=='buy' and bot(x) in BADDAY}
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float))]
J=[]
for s in sells:
    em=(buys.get((bot(s),(s.get('address')or s.get('token')or'').lower())) or {}).get('entry_meta') or {}
    J.append((s.get('pnl_pct'),em))
N=len(J); base_sum=sum(p for p,_ in J); base_mean=base_sum/N
print(f"BASE N={N} mean={base_mean:.2f} sum={base_sum:.0f} winrate={sum(1 for p,_ in J if p>0)/N:.3f}")

def evalblock(mask):
    blk=[p for (p,_),m in zip(J,mask) if m]
    kept=[p for (p,_),m in zip(J,mask) if not m]
    if not blk: return None
    lb=sum(1 for p in blk if p<=0); wb=len(blk)-lb
    rem=sum(blk)
    km_before=base_mean
    km_after=(sum(kept)/len(kept)) if kept else 0
    return dict(n=len(blk),losers=lb,winners=wb,removed=round(rem,1),
                kept_mean_after=round(km_after,2),km_before=round(km_before,2),
                lift=round(km_after-km_before,2))

def num(em,k):
    v=em.get(k)
    return v if isinstance(v,(int,float)) else None

def scan_field(field, lo_hi='hi', grid=None):
    vals=[num(em,field) for _,em in J]
    present=[v for v in vals if v is not None]
    if len(present)<50: 
        print(f"  {field}: low coverage {len(present)}"); return []
    qs=st.quantiles(present, n=10)
    results=[]
    cands=grid if grid else qs
    for thr in cands:
        # block ABOVE thr
        m=[(v is not None and v>=thr) for v in vals]
        r=evalblock(m)
        if r and r['n']>=8:
            results.append(('>=',field,round(thr,4),r))
        # block BELOW thr
        m=[(v is not None and v<thr) for v in vals]
        r=evalblock(m)
        if r and r['n']>=8:
            results.append(('<',field,round(thr,4),r))
    return results

FIELDS=['liquidity_usd','lifecycle_age_hours','bs_m5','net_flow_15s_imbalance','net_flow_15s_usd',
        'unique_buyers_n','n_recurring_buyers_3plus','vol_h1_accel_vs_h6','pc_h6','pc_h1','pc_h24',
        'entry_volume_h24_usd','turnover_h24_ratio','rt_dollar_imbalance','net_flow_60s_imbalance',
        'buy_pressure_60s','rsi_5m','top10_holder_pct','unique_buyer_ratio','top5_buyer_volume_pct']
all_r=[]
for f in FIELDS:
    all_r += scan_field(f)
# rank by: removed strongly negative AND loser:winner ratio high AND lift positive
def score(r):
    op,f,thr,d=r
    if d['removed']>=0: return -1e9  # reject killing net winners
    lr = d['losers']/(d['winners']+0.5)
    return (-d['removed']) * lr * (1 if d['lift']>0 else 0.3)
all_r.sort(key=score, reverse=True)
print("\nTOP SINGLE-THRESHOLD (removed<0, sorted by surgical loser-kill):")
for r in all_r[:18]:
    op,f,thr,dd=r
    print(f"  {f} {op}{thr}: n={dd['n']} L={dd['losers']} W={dd['winners']} removed={dd['removed']} lift={dd['lift']} kept_after={dd['kept_mean_after']}")

print("\n=== 2-WAY INTERSECTIONS ===")
def mask_field(field, op, thr):
    out=[]
    for _,em in J:
        v=num(em,field)
        if v is None: out.append(False); continue
        out.append(v>=thr if op=='>=' else v<thr)
    return out

# candidate axes (op, field, threshold grid)
AX={
 'age_lo':('lifecycle_age_hours','<',[25,40,60]),
 'turn_hi':('turnover_h24_ratio','>=',[15,19,20.6]),
 'vol_hi':('entry_volume_h24_usd','>=',[600000,883000]),
 'pch6_lo':('pc_h6','<',[-15,-22,-32]),
 'pch1_lo':('pc_h1','<',[-30,-38]),
 'top10_lo':('top10_holder_pct','<',[55,60]),
 'ubr_lo':('unique_buyer_ratio','<',[0.7]),
 'liq_lo':('liquidity_usd','<',[27000,35000]),
 'nf15_lo':('net_flow_15s_imbalance','<',[0.1,0.2]),
 'bsm5_lo':('bs_m5','<',[1.0,1.2]),
}
import itertools
combos=[]
keys=list(AX)
for a,b in itertools.combinations(keys,2):
    fa,oa,ga=AX[a]; fb,ob,gb=AX[b]
    if fa==fb: continue
    for ta in ga:
        for tb in gb:
            ma=mask_field(fa,oa,ta); mb=mask_field(fb,ob,tb)
            m=[x and y for x,y in zip(ma,mb)]
            r=evalblock(m)
            if r and r['n']>=8 and r['removed']<0:
                lr=r['losers']/(r['winners']+0.5)
                combos.append((lr, f"{fa}{oa}{ta} & {fb}{ob}{tb}", r))
combos.sort(key=lambda x:(x[0], -x[2]['removed']), reverse=True)
seen=0
for lr,desc,r in combos:
    if r['losers']<6: continue
    print(f"  {desc}: n={r['n']} L={r['losers']} W={r['winners']} L:W={lr:.2f} removed={r['removed']} lift={r['lift']} kept_after={r['kept_mean_after']}")
    seen+=1
    if seen>=20: break
