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
pond=[]; everything=[]
for t in trades:
    if t.get("type")!="sell": continue
    if "cancelled on restart" in (t.get("reason") or "").lower(): continue
    k=((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
    c=[b for b in bb.get(k,[]) if b.get("time","")<t.get("time","")]
    if not c: continue
    b=c[-1]; em=b.get("entry_meta") or {}
    s1=em.get("sol_pc_h1"); s6=em.get("sol_pc_h6")
    if not isinstance(s1,(int,float)) or not isinstance(s6,(int,float)): continue
    row=(float(t.get("pnl_pct") or 0), float(t.get("pnl") or 0), s1, s6)
    everything.append(row)
    if stack_pass(b): pond.append(row)

def bands(rows, key_idx, cuts, labels):
    print(f"{'band':22s}{'n':>6s}{'WR':>6s}{'$/tr':>8s}{'%/tr':>8s}")
    for lo,hi,lab in zip(cuts[:-1],cuts[1:],labels):
        sel=[r for r in rows if lo<=r[key_idx]<hi]
        if len(sel)<25: 
            print(f"  {lab:20s}{len(sel):6d}   (thin)"); continue
        wr=sum(1 for r in sel if r[0]>0)/len(sel)
        print(f"  {lab:20s}{len(sel):6d}{wr*100:5.0f}%{statistics.mean(r[1] for r in sel):+8.2f}{statistics.mean(r[0] for r in sel):+8.2f}")

print(f"=== IN-POND (stack-passers, n={len(pond)}) by SOL h1 at entry ===")
print("current gate blocks sol_h1 < -0.7 (most bots)")
bands(pond, 2, [-99,-1.5,-0.7,-0.3,0,0.3,0.7,99],
      ["< -1.5 (blocked)","-1.5..-0.7 (blocked)","-0.7..-0.3","-0.3..0","0..0.3","0.3..0.7","> 0.7"])
print(f"\n=== IN-POND by SOL h6 at entry (gate blocks h6 < -0.3) ===")
bands(pond, 3, [-99,-3,-1,-0.3,0,1,3,99],
      ["< -3 (blocked)","-3..-1 (blocked)","-1..-0.3 (blocked)","-0.3..0","0..1","1..3","> 3"])
print(f"\n=== ALL JOINED trades (n={len(everything)}) by SOL h1 — sanity/contrast ===")
bands(everything, 2, [-99,-1.5,-0.7,0,0.7,99],
      ["< -1.5","-1.5..-0.7","-0.7..0","0..0.7","> 0.7"])
