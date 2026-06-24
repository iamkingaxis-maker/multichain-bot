import json
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float)) and isinstance(x.get('peak_pnl_pct'),(int,float))]
win_p3=[s for s in sells if s['peak_pnl_pct']>=3 and s['pnl_pct']>0]
clip=[s for s in win_p3 if isinstance(s.get('mae_pct'),(int,float)) and s['mae_pct']<=0 and isinstance(s.get('mae_at_secs'),(int,float))]

# The arming logic: peak>=3 reached, THEN drop to <=0 fires exit. The decisive question is
# whether the <=0 touch (mae) occurred BEFORE or AFTER the +3 peak was first reached.
# We don't have time-of-peak. But mae<=0 touches that happen >60s into a multi-min hold,
# where the trade FINISHED well green, are strong candidates for genuine post-peak round-trips.
# Conservative-for-the-CANDIDATE read: only count as winner-kill those where the negative
# excursion is BOTH late AND deep enough that it's implausibly the entry dip.

import statistics
# Bucket by how deep mae went
deep = [s for s in clip if s['mae_pct'] <= -0.5 and s['mae_at_secs']>30]
print("winners peak>=3 that touched <=-0.5%% AFTER 30s (robust post-peak red touch):",len(deep))
print("  their pnl upside that would be forfeited if clipped to ~0:",round(sum(s['pnl_pct'] for s in deep),2))
print("  distinct of these:",len(set((s.get('address')or'')[:8] for s in deep)))
print("  pnl distribution:",sorted(round(s['pnl_pct'],1) for s in deep))

# Even the strictest interpretation: a winner that touched <=0 (mae_pct<=0) and is NOT an
# early-entry-dip. The candidate said min winner FINAL is +0.61 => floor never clips a winner.
# That conflates FINAL with INTRA-TRADE. Show the contradiction crisply:
touched0 = [s for s in win_p3 if isinstance(s.get('mae_pct'),(int,float)) and s['mae_pct']<=0]
print("\nCONTRADICTION: candidate claims winners 'never round-trip below breakeven'")
print("  but %d of %d winners (%.0f%%) have mae_pct<=0 (DID touch breakeven/red intra-trade)"%(
    len(touched0),len(win_p3),100*len(touched0)/len(win_p3)))
