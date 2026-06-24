import json
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float))]

# A leg is "blocked" by mae_floor_7 if its MAE touched <= -7 (the floor would have fired)
blocked=[s for s in sells if isinstance(s.get('mae_pct'),(int,float)) and s['mae_pct']<=-7.0]
print("blocked_n (mae<=-7):", len(blocked))
bw=[s for s in blocked if s['pnl_pct']>0]
bl=[s for s in blocked if s['pnl_pct']<=0]
print("winners_blocked:", len(bw), "losers_blocked:", len(bl))
removed_pnl=sum(s['pnl_pct'] for s in blocked)
print("removed_pnl_pct (sum final pnl of blocked):", round(removed_pnl,1))
print("mean final pnl of blocked:", round(removed_pnl/len(blocked),2))

# realized saving: if we exit at -7 instead of their actual final pnl
# saving per leg = -7 - final_pnl (positive when final < -7)
sav=sum((-7.0 - s['pnl_pct']) for s in blocked)
print("realized saving by bailing at -7 (sum):", round(sav,1), " per-leg:", round(sav/len(blocked),2))
# how many actually exit worse than -7
worse=[s for s in blocked if s['pnl_pct'] < -7.0]
print("blocked legs that finally exit worse than -7:", len(worse), "of", len(blocked))

# kept-mean before/after (on all sells)
all_pnl=[s['pnl_pct'] for s in sells]
print("kept_mean_before (all):", round(sum(all_pnl)/len(all_pnl),2))
kept=[s for s in sells if not(isinstance(s.get('mae_pct'),(int,float)) and s['mae_pct']<=-7.0)]
kp=[s['pnl_pct'] for s in kept]
print("kept_mean_after (remove blocked):", round(sum(kp)/len(kp),2))

# winner-kill pct
winners=[s for s in sells if s['pnl_pct']>0]
print("total_winners:", len(winners), "winner_kill_pct:", round(100*len(bw)/len(winners),2))
