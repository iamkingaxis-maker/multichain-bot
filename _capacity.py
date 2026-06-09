import json, collections, statistics, sys
from datetime import datetime, timedelta
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
COMBOS={
 "settled_flow": lambda em: (isinstance(em.get("shape_30m_mins_since_max"),(int,float)) and em["shape_30m_mins_since_max"]>23
                              and isinstance(em.get("net_flow_60s_imbalance"),(int,float)) and em["net_flow_60s_imbalance"]>0.365),
 "settled_flow_thin": lambda em: (isinstance(em.get("shape_30m_mins_since_max"),(int,float)) and em["shape_30m_mins_since_max"]>23
                              and isinstance(em.get("net_flow_60s_imbalance"),(int,float)) and em["net_flow_60s_imbalance"]>0.365
                              and isinstance(em.get("slip_buy_2000_pct"),(int,float)) and em["slip_buy_2000_pct"]>1.709),
 "ugly_mtf": lambda em: (isinstance(em.get("chart_score"),(int,float)) and em["chart_score"]<50
                              and isinstance(em.get("chart_mtf_score"),(int,float)) and em["chart_mtf_score"]<0),
}
# per-day DISTINCT passing tokens + per-token first-entry %-outcome (clone enters once/token)
stats=collections.defaultdict(lambda: collections.defaultdict(dict))  # combo -> day -> token -> pct
for t in trades:
    if t.get("type")!="sell": continue
    if "cancelled on restart" in (t.get("reason") or "").lower(): continue
    if (t.get("time") or "")<"2026-05-26": continue
    k=((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
    c=[b for b in bb.get(k,[]) if b.get("time","")<t.get("time","")]
    if not c: continue
    b=c[-1]
    if not stack_pass(b): continue
    em=b.get("entry_meta") or {}
    try: day=(datetime.fromisoformat(str(t.get("time")).replace("Z","+00:00"))-timedelta(hours=5)).strftime("%m-%d")
    except: continue
    tok=t.get("token") or ""
    pct=float(t.get("pnl_pct") or 0)
    for nm,f in COMBOS.items():
        if f(em):
            # keep the MEAN pct per token-day (a clone's position outcome proxy)
            cur=stats[nm][day].get(tok,[])
            cur.append(pct); stats[nm][day][tok]=cur
print("CAPACITY MODEL @ $100/position, one position per passing token per day:")
print(f"{'combo':20s}{'days':>5s}{'tok/day':>8s}{'mean%/tok':>10s}{'$/day @100':>11s}")
total=0
for nm in COMBOS:
    days=stats[nm]
    if not days: continue
    tokperday=[len(v) for v in days.values()]
    tokmeans=[statistics.mean(p) for v in days.values() for p in [[statistics.mean(x) for x in v.values()]]]
    # flatten: mean pct across token-days
    all_tok_pcts=[statistics.mean(x) for v in days.values() for x in v.values()]
    mtd=statistics.mean(tokperday); mp=statistics.mean(all_tok_pcts)
    dpd=mtd*100*mp/100
    total+=dpd
    print(f"  {nm:18s}{len(days):5d}{mtd:8.1f}{mp:+10.2f}{dpd:+11.0f}")
print(f"\n3-clone pond capacity ≈ ${total:+.0f}/day (overlap not deduped — upper bound)")
print("plus pool_c_post_peak ~ +$3/day proven, smart_follow variable")
