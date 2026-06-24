import json
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float))]
print("total badday sells with pnl_pct:",len(sells))

# winners/losers overall
winners=[s for s in sells if s['pnl_pct']>0]
losers=[s for s in sells if s['pnl_pct']<=0]
print("total winners:",len(winners),"total losers:",len(losers))

# candidate: peak_pnl_pct >= +3 then final pnl_pct <= 0  => those would be force-exited at breakeven
# The blocked cohort = trades that peaked>=3 AND closed <=0. Rule forces exit at ~0 instead.
have_peak=[s for s in sells if isinstance(s.get('peak_pnl_pct'),(int,float))]
print("sells with peak_pnl_pct:",len(have_peak))

cohort=[s for s in have_peak if s['peak_pnl_pct']>=3 and s['pnl_pct']<=0]
print("\n=== COHORT: peak>=3 AND final<=0 (would be forced to breakeven) ===")
print("blocked_n:",len(cohort))
print("losers_blocked (final<=0):",sum(1 for s in cohort if s['pnl_pct']<=0))
print("winners_blocked (final>0):",sum(1 for s in cohort if s['pnl_pct']>0))
print("removed_pnl_pct (sum of final pnl as-is):",round(sum(s['pnl_pct'] for s in cohort),2))

# distinct tokens
toks=set((s.get('address') or s.get('token') or '').lower() for s in cohort)
print("distinct tokens:",len(toks))
from collections import Counter
print("per-token count:",Counter((s.get('address') or s.get('token') or '')[:8] for s in cohort).most_common(15))
print("per-bot:",Counter(bot(s) for s in cohort).most_common())
