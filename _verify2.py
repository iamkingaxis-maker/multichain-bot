import json
from collections import Counter
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float)) and isinstance(x.get('peak_pnl_pct'),(int,float))]

# ALL trades that peaked >=3 (winners and losers) - is the floor=0 actually clean for winners?
peaked3=[s for s in sells if s['peak_pnl_pct']>=3]
print("all trades peak>=3:",len(peaked3))
win_p3=[s for s in peaked3 if s['pnl_pct']>0]
los_p3=[s for s in peaked3 if s['pnl_pct']<=0]
print("  of those, final>0:",len(win_p3)," final<=0:",len(los_p3))
print("  min final among final>0 (winner):",round(min(s['pnl_pct'] for s in win_p3),3))
# distribution of winners' final near zero
near=sorted(s['pnl_pct'] for s in win_p3)[:15]
print("  lowest 15 winner finals:",[round(x,2) for x in near])

# CRITICAL: does forcing exit at 0 actually save? The rule exits "when pnl drops back to <=0".
# It would realize ~0% (minus slippage), not the final negative. Saving = -(final pnl) per loser.
saved=-sum(s['pnl_pct'] for s in los_p3)
print("\nP&L saved by exiting at 0 vs letting bleed:", round(saved,2),"pnl_pct points")

# winner-kill: if a winner ever dipped to <=0 AFTER peaking>=3, the rule would have force-exited it
# We can't see intra-trade path, but mae_pct (max adverse) tells if it went negative.
# A winner that peaked>=3 but had mae after the peak below 0 would be clipped.
# mae_pct is max adverse over whole trade. If mae_pct<=0 for a winner, it touched <=0 at some point.
print("\n=== WINNER-KILL RISK via mae_pct (touched <=0 sometime) ===")
clip_risk=[s for s in win_p3 if isinstance(s.get('mae_pct'),(int,float)) and s['mae_pct']<=0]
print("winners peak>=3 with mae_pct<=0 (could be clipped to ~0):",len(clip_risk),"of",len(win_p3))
for s in clip_risk[:20]:
    print("  final=%+.2f peak=%+.2f mae=%+.2f mae_at_secs=%s hold=%s"%(s['pnl_pct'],s['peak_pnl_pct'],s['mae_pct'],s.get('mae_at_secs'),s.get('hold_secs')))
