import json
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float)) and isinstance(x.get('peak_pnl_pct'),(int,float))]
win_p3=[s for s in sells if s['peak_pnl_pct']>=3 and s['pnl_pct']>0]

# The arming sequence: peak>=3 must be reached BEFORE the dip to <=0 for the rule to clip.
# We don't have full path. mae_at_secs vs when peak hit (unknown). But: if mae_pct<=0 AND the
# winner ultimately finished well above 0 having peaked high, the dip-to-0 could be EITHER
# before the +3 peak (rule not armed, safe) or after (rule clips at ~0, losing the rest).
# Conservative adversarial read: count winners where mae<=0 as POTENTIAL clips, and look at
# mae_at_secs early (dip happened early, likely before peak => safe) vs late.

# Heuristic: if mae_at_secs is very early (e.g. <=15s) the negative excursion is likely the
# entry-slip dip BEFORE the run up => arming not yet triggered => SAFE.
# If the negative mae is LATE in a long hold, it's a post-peak round trip => rule would clip.
clip=[s for s in win_p3 if isinstance(s.get('mae_pct'),(int,float)) and s['mae_pct']<=0]
early=[s for s in clip if isinstance(s.get('mae_at_secs'),(int,float)) and s['mae_at_secs']<=15]
late=[s for s in clip if isinstance(s.get('mae_at_secs'),(int,float)) and s['mae_at_secs']>15]
print("winners peak>=3, mae<=0 total:",len(clip))
print("  early dip (<=15s, likely pre-peak, SAFE):",len(early))
print("  late dip (>15s, likely post-peak => CLIPPED):",len(late))
print("  pnl that would be FORFEITED if late ones clipped to ~0:",round(sum(s['pnl_pct'] for s in late),2))

# Even tighter: among 'late', those where the dip is unambiguously after a +3 peak we cannot
# prove, but the EXISTENCE of substantial late-dipping winners refutes 'never round-trips'.
print("\nsample LATE-dip winners (post-peak round trip into red, finished green):")
for s in sorted(late,key=lambda x:-x['pnl_pct'])[:15]:
    print("  final=%+.2f peak=%+.2f mae=%+.2f mae_at_secs=%.0f hold=%.0f bot=%s"%(
        s['pnl_pct'],s['peak_pnl_pct'],s['mae_pct'],s['mae_at_secs'],s.get('hold_secs',0),bot(s)))

# NET if we believe late ones get clipped: gained 274.48 (losers saved) minus forfeited winner upside
forfeit=sum(s['pnl_pct'] for s in late)
print("\nNET (saved 274.48 - winner upside forfeited %.2f) = %+.2f"%(forfeit,274.48-forfeit))
print("winner_kill_pct = %d / 262 = %.1f%%"%(len(late),100*len(late)/262))
