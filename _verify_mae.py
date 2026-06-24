import json
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
buys={(bot(x),(x.get('address')or x.get('token')or'').lower()):x for x in t if isinstance(x,dict) and x.get('type')=='buy' and bot(x) in BADDAY}
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float))]
print("total sells (legs):", len(sells))

# look at MAE availability
mae_present=[s for s in sells if isinstance(s.get('mae_pct'),(int,float))]
print("sells with mae_pct:", len(mae_present))

# winners vs losers
winners=[s for s in sells if s['pnl_pct']>0]
losers=[s for s in sells if s['pnl_pct']<=0]
print("winners:", len(winners), "losers:", len(losers))

# MAE of winners
win_mae=[s['mae_pct'] for s in winners if isinstance(s.get('mae_pct'),(int,float))]
los_mae=[s['mae_pct'] for s in losers if isinstance(s.get('mae_pct'),(int,float))]
print("winners with mae:", len(win_mae), "losers with mae:", len(los_mae))
if win_mae:
    print("worst (min) winner MAE:", min(win_mae), " max(closest to 0):", max(win_mae))
    # how many winners have mae <= -7 (would be killed by floor)
    wk = [m for m in win_mae if m<=-7.0]
    print("winners with mae<=-7 (WINNER KILL):", len(wk))
    wk65 = [m for m in win_mae if m<=-6.5]
    print("winners with mae<=-6.5:", len(wk65))
