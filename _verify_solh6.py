import json
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
buys={(bot(x),(x.get('address')or x.get('token')or'').lower()):x for x in t if isinstance(x,dict) and x.get('type')=='buy' and bot(x) in BADDAY}
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float))]

rows=[]
miss=0
for s in sells:
    key=(bot(s),(s.get('address')or s.get('token')or'').lower())
    em=(buys.get(key) or {}).get('entry_meta') or {}
    h6=em.get('sol_pc_h6'); h1=em.get('sol_pc_h1')
    rows.append((s['pnl_pct'],h6,h1))

n=len(rows)
print("total joined sells:",n)
# missingness for sol fields
have_h6=sum(1 for p,h6,h1 in rows if isinstance(h6,(int,float)))
have_h1=sum(1 for p,h6,h1 in rows if isinstance(h1,(int,float)))
have_both=sum(1 for p,h6,h1 in rows if isinstance(h6,(int,float)) and isinstance(h1,(int,float)))
print("have sol_pc_h6:",have_h6," have sol_pc_h1:",have_h1," have both:",have_both)

# the rule: h6 < -1.0 AND h1 < 0
def blocked(h6,h1):
    return isinstance(h6,(int,float)) and isinstance(h1,(int,float)) and h6 < -1.0 and h1 < 0

bl=[(p,h6,h1) for p,h6,h1 in rows if blocked(h6,h1)]
kept=[(p,h6,h1) for p,h6,h1 in rows if not blocked(h6,h1)]
total_winners=sum(1 for p,h6,h1 in rows if p>0)
total_losers=sum(1 for p,h6,h1 in rows if p<=0)
print("\n--- RULE: sol_pc_h6 < -1.0 AND sol_pc_h1 < 0 ---")
print("blocked_n:",len(bl))
print("losers_blocked:",sum(1 for p,h6,h1 in bl if p<=0))
print("winners_blocked:",sum(1 for p,h6,h1 in bl if p>0))
print("removed_pnl_pct:",round(sum(p for p,h6,h1 in bl),2))
import statistics as st
print("kept_mean_before(all):",round(st.mean([p for p,h6,h1 in rows]),3))
print("kept_mean_after:",round(st.mean([p for p,h6,h1 in kept]),3) if kept else None)
print("total_winners:",total_winners," total_losers:",total_losers)
wb=sum(1 for p,h6,h1 in bl if p>0)
print("winner_kill_pct:",round(100*wb/total_winners,2) if total_winners else None)
print("blocked mean pnl:",round(st.mean([p for p,h6,h1 in bl]),3) if bl else None)
print("blocked median pnl:",round(st.median([p for p,h6,h1 in bl]),3) if bl else None)
