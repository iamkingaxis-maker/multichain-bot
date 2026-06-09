import json, collections, statistics, sys
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
pond=[]
for t in trades:
    if t.get("type")!="sell": continue
    if "cancelled on restart" in (t.get("reason") or "").lower(): continue
    k=((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
    c=[b for b in bb.get(k,[]) if b.get("time","")<t.get("time","")]
    if not c: continue
    b=c[-1]; em=b.get("entry_meta") or {}
    s1=em.get("sol_pc_h1")
    if not isinstance(s1,(int,float)): continue
    if not stack_pass(b): continue
    pond.append((t.get("time",""), float(t.get("pnl_pct") or 0), float(t.get("pnl") or 0), s1, t.get("token") or ""))
pond.sort(key=lambda r:r[0])
half=len(pond)//2
for name,rows in (("TRAIN (first half)",pond[:half]),("TEST (second half)",pond[half:])):
    keep=[r for r in rows if r[3]<=0.3]; cut=[r for r in rows if r[3]>0.3]
    wrk=sum(1 for r in keep if r[1]>0)/len(keep); wrc=sum(1 for r in cut if r[1]>0)/len(cut)
    print(f"{name}: n={len(rows)}")
    print(f"  KEEP (sol_h1<=+0.3): n={len(keep)} ({len({r[4] for r in keep})} tok) WR={wrk*100:.0f}% ${statistics.mean(r[2] for r in keep):+.2f}/tr")
    print(f"  CUT  (sol_h1> +0.3): n={len(cut)} ({len({r[4] for r in cut})} tok) WR={wrc*100:.0f}% ${statistics.mean(r[2] for r in cut):+.2f}/tr")
    saved=-sum(r[2] for r in cut)
    print(f"  -> blocking green side would change P&L by {saved:+.0f} on this half\n")
