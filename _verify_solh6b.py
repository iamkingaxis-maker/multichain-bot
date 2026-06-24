import json, statistics as st
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
buys={(bot(x),(x.get('address')or x.get('token')or'').lower()):x for x in t if isinstance(x,dict) and x.get('type')=='buy' and bot(x) in BADDAY}
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float))]
rows=[]
for s in sells:
    key=(bot(s),(s.get('address')or s.get('token')or'').lower())
    em=(buys.get(key) or {}).get('entry_meta') or {}
    rows.append((s['pnl_pct'],em.get('sol_pc_h6'),em.get('sol_pc_h1')))

def blocked(h6,h1):
    return isinstance(h6,(int,float)) and isinstance(h1,(int,float)) and h6 < -1.0 and h1 < 0
bl=sorted([p for p,h6,h1 in rows if blocked(h6,h1)])
print("blocked pnls sorted:",[round(x,1) for x in bl])
print("sum top-3 worst:",round(sum(bl[:3]),2)," sum rest:",round(sum(bl[3:]),2))
# remove the 3 fattest losers, does the cohort still net negative?
print("removed_pnl ex-worst3:",round(sum(bl[3:]),2)," n:",len(bl[3:]))

# Threshold sensitivity: scan h6 cut and h1 cut
print("\n--- THRESHOLD SENSITIVITY (h6<X AND h1<0) ---")
for X in [-0.5,-1.0,-1.5,-2.0,-3.0]:
    c=[(p) for p,h6,h1 in rows if isinstance(h6,(int,float)) and isinstance(h1,(int,float)) and h6<X and h1<0]
    w=sum(1 for p in c if p>0); l=sum(1 for p in c if p<=0)
    print(f"h6<{X}: n={len(c)} L={l} W={w} removedPnl={round(sum(c),1)} mean={round(st.mean(c),2) if c else None}")

print("\n--- h6<-1.0 alone (drop the h1 condition) ---")
c=[p for p,h6,h1 in rows if isinstance(h6,(int,float)) and h6<-1.0]
w=sum(1 for p in c if p>0); l=sum(1 for p in c if p<=0)
print(f"n={len(c)} L={l} W={w} removedPnl={round(sum(c),1)} mean={round(st.mean(c),2) if c else None}")

print("\n--- h1<0 alone (drop h6) ---")
c=[p for p,h6,h1 in rows if isinstance(h1,(int,float)) and h1<0]
w=sum(1 for p in c if p>0); l=sum(1 for p in c if p<=0)
print(f"n={len(c)} L={l} W={w} removedPnl={round(sum(c),1)} mean={round(st.mean(c),2) if c else None}")

# per-bot breakdown of the blocked cohort
print("\n--- per-bot blocked cohort ---")
from collections import defaultdict
pb=defaultdict(list)
for s in sells:
    key=(bot(s),(s.get('address')or s.get('token')or'').lower())
    em=(buys.get(key) or {}).get('entry_meta') or {}
    h6=em.get('sol_pc_h6'); h1=em.get('sol_pc_h1')
    if blocked(h6,h1): pb[bot(s)].append(s['pnl_pct'])
for k,v in pb.items():
    print(k,"n=",len(v),"removedPnl=",round(sum(v),1),"L=",sum(1 for p in v if p<=0),"W=",sum(1 for p in v if p>0))
