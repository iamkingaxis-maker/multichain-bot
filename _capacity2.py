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
 "flow_thin (pair)": lambda em: (isinstance(em.get("net_flow_60s_imbalance"),(int,float)) and em["net_flow_60s_imbalance"]>0.365
                              and isinstance(em.get("slip_buy_2000_pct"),(int,float)) and em["slip_buy_2000_pct"]>1.709),
 "bb_low+mtf (pair)": lambda em: (isinstance(em.get("bb_pos_15m"),(int,float)) and em["bb_pos_15m"]<0.452
                              and isinstance(em.get("chart_mtf_score"),(int,float)) and em["chart_mtf_score"]<0),
 "sweep+flow (pair)": lambda em: (isinstance(em.get("chart_sweep_5m_low_candles_ago"),(int,float)) and em["chart_sweep_5m_low_candles_ago"]>1
                              and isinstance(em.get("net_flow_60s_imbalance"),(int,float)) and em["net_flow_60s_imbalance"]>0.365),
}
stats=collections.defaultdict(lambda: collections.defaultdict(dict))
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
    for nm,f in COMBOS.items():
        if f(em):
            stats[nm][day].setdefault(tok,[]).append(float(t.get("pnl_pct") or 0))
print("WIDER combos capacity @ $100/position, 1 position/token/day (held-out-validated pairs):")
print(f"{'combo':20s}{'days':>5s}{'tok/day':>8s}{'mean%/tok':>10s}{'WR(tok)':>8s}{'$/day':>7s}")
for nm in COMBOS:
    days=stats[nm]
    if not days: print(f"  {nm}: none"); continue
    tokperday=[len(v) for v in days.values()]
    all_tok=[statistics.mean(x) for v in days.values() for x in v.values()]
    wr=sum(1 for x in all_tok if x>0)/len(all_tok)
    mtd=statistics.mean(tokperday); mp=statistics.mean(all_tok)
    print(f"  {nm:18s}{len(days):5d}{mtd:8.1f}{mp:+10.2f}{wr*100:7.0f}%{mtd*mp:+7.0f}")
# time-split robustness for flow_thin pair at token-day level
nm="flow_thin (pair)"
days=sorted(stats[nm])
h=len(days)//2
for label,ds in (("first half",days[:h]),("second half",days[h:])):
    all_tok=[statistics.mean(x) for d2 in ds for x in stats[nm][d2].values()]
    if not all_tok: continue
    print(f"  flow_thin {label}: tok={len(all_tok)} mean%={statistics.mean(all_tok):+.2f} WR={sum(1 for x in all_tok if x>0)/len(all_tok)*100:.0f}%")
