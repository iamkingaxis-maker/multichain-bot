import json, collections, statistics, itertools, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
d=json.load(open("_bleed_trades.json"))
trades=d if isinstance(d,list) else d.get("trades",[])
bb=collections.defaultdict(list)
for t in trades:
    if t.get("type")=="buy":
        k=((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
        bb[k].append(t)
for k in bb: bb[k].sort(key=lambda b:b.get("time",""))
def stack_pass(b):
    em=b.get("entry_meta") or {}
    v=em.get("shape_90m_drawdown_from_max_pct")
    if isinstance(v,(int,float)) and v>-16: return False
    v=em.get("net_flow_60s_usd")
    if isinstance(v,(int,float)) and v<100: return False
    v=b.get("entry_age_hours")
    if isinstance(v,(int,float)) and 0<v<24: return False
    v=b.get("entry_market_cap_usd")
    if isinstance(v,(int,float)) and v>0 and not (5e5<=v<=1e7): return False
    return True
rows=[]
for t in trades:
    if t.get("type")!="sell": continue
    if "cancelled on restart" in (t.get("reason") or "").lower(): continue
    k=((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
    c=[b for b in bb.get(k,[]) if b.get("time","")<t.get("time","")]
    if not c: continue
    b=c[-1]
    if not stack_pass(b): continue
    rows.append((t.get("time",""), float(t.get("pnl_pct") or 0), float(t.get("pnl") or 0), b.get("entry_meta") or {}, t.get("token") or ""))
rows.sort(key=lambda r:r[0])
half=len(rows)//2; train,test=rows[:half],rows[half:]

# the 11 held-out survivors as predicates (train-median thresholds, frozen)
PREDS={
 "settled_dip":  lambda em: isinstance(em.get("shape_30m_mins_since_max"),(int,float)) and em["shape_30m_mins_since_max"]>23,
 "sweep_reclaim":lambda em: isinstance(em.get("chart_sweep_5m_low_candles_ago"),(int,float)) and em["chart_sweep_5m_low_candles_ago"]>1,
 "deep_60m":     lambda em: isinstance(em.get("shape_60m_chg_pct"),(int,float)) and em["shape_60m_chg_pct"]<-12.1,
 "flow_imbal":   lambda em: isinstance(em.get("net_flow_60s_imbalance"),(int,float)) and em["net_flow_60s_imbalance"]>0.365,
 "bb_low":       lambda em: isinstance(em.get("bb_pos_15m"),(int,float)) and em["bb_pos_15m"]<0.452,
 "rsi5_os":      lambda em: isinstance(em.get("rsi_5m"),(int,float)) and em["rsi_5m"]<41.08,
 "ugly_chart":   lambda em: isinstance(em.get("chart_score"),(int,float)) and em["chart_score"]<50,
 "rsi15_os":     lambda em: isinstance(em.get("rsi_15m"),(int,float)) and em["rsi_15m"]<48.61,
 "mtf_neg":      lambda em: isinstance(em.get("chart_mtf_score"),(int,float)) and em["chart_mtf_score"]<0,
 "thin_book":    lambda em: isinstance(em.get("slip_buy_2000_pct"),(int,float)) and em["slip_buy_2000_pct"]>1.709,
}
def evalc(combo, data):
    sel=[(p,u,tok) for _,p,u,em,tok in data if all(PREDS[c](em) for c in combo)]
    if len(sel)<40: return None
    wr=sum(1 for p,_,_ in sel if p>0)/len(sel)
    dpt=statistics.mean(u for _,u,_ in sel)
    ntok=len({tok for _,_,tok in sel})
    return wr,dpt,len(sel),ntok
base_te=sum(1 for r in test if r[1]>0)/len(test)
print(f"test baseline WR {base_te*100:.0f}% | n={len(test)} | $/tr {statistics.mean(r[2] for r in test):+.2f}")
print(f"\n{'combo':46s}{'trWR':>6s}{'teWR':>6s}{'te$/tr':>8s}{'teN':>5s}{'tok':>5s}")
results=[]
for r in (2,3):
    for combo in itertools.combinations(PREDS,r):
        tr=evalc(combo,train); te=evalc(combo,test)
        if not tr or not te: continue
        if tr[0]<0.72: continue          # train bar
        results.append((combo,tr,te))
results.sort(key=lambda x:-x[2][0])
seen=0
for combo,tr,te in results:
    if seen>=18: break
    seen+=1
    print(f"  {'+'.join(combo):44s}{tr[0]*100:5.0f}%{te[0]*100:5.0f}%{te[1]:+8.2f}{te[2]:5d}{te[3]:5d}")
# Pareto: WR vs throughput on TEST
print("\nPareto frontier (test WR vs test n, $-positive only):")
pareto=[]
for combo,tr,te in results:
    if te[1]<=0: continue
    if not any(o[2][0]>=te[0] and o[2][2]>te[2] and o is not (combo,tr,te) for o in results if o[2][1]>0):
        pareto.append((combo,te))
pareto.sort(key=lambda x:-x[1][0])
for combo,te in pareto[:8]:
    perday=te[2]/14.0
    print(f"  {'+'.join(combo):44s} WR={te[0]*100:.0f}% ${te[1]:+.2f}/tr n={te[2]} (~{perday:.0f} tr/day fleet) tokens={te[3]}")
