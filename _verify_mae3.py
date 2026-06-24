import json
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float)) and isinstance(x.get('mae_pct'),(int,float))]

# sensitivity of winner-kill across thresholds
winners=[s for s in sells if s['pnl_pct']>0]
losers=[s for s in sells if s['pnl_pct']<=0]
for thr in [-5.85,-6.0,-6.5,-7.0,-7.5,-8.0]:
    wk=len([s for s in winners if s['mae_pct']<=thr])
    lb=len([s for s in losers if s['mae_pct']<=thr])
    print(f"thr={thr}: winners_killed={wk} losers_blocked={lb}")

# margin: how close is the nearest winner MAE to -7?
win_mae=sorted([s['mae_pct'] for s in winners])
print("5 worst winner MAEs:", [round(x,2) for x in win_mae[:5]])
# nearest loser MAE just below -7 region
los_mae=sorted([s['mae_pct'] for s in losers])
near=[round(x,2) for x in los_mae if -8.5<=x<=-6.0]
print("loser MAEs in [-8.5,-6.0]:", near)

# per-bot breakdown of blocked
from collections import Counter
blocked=[s for s in sells if s['mae_pct']<=-7.0]
print("blocked by bot:", dict(Counter(bot(s) for s in blocked)))
