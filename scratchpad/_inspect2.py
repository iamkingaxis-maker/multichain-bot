import json
from collections import Counter
d=json.load(open('_trades_cache.json'))
bd=[r for r in d if r.get('bot_id','').startswith('badday_')]
buys=[r for r in bd if r['type']=='buy']
sells=[r for r in bd if r['type']=='sell']
print('badday buys:',len(buys),'sells:',len(sells))
# union of sell keys
sk=Counter()
for r in sells:
    for k in r: sk[k]+=1
print('sell keys:',dict(sk))
# check mae fields
print('has mae_pct:',sum('mae_pct' in r for r in sells))
print('has mae_at_secs:',sum('mae_at_secs' in r for r in sells))
# time range of buys
times=sorted(r['time'] for r in buys)
print('buy time range:',times[0],'->',times[-1])
# window >=2026-07-03
recent=[r for r in buys if r['time']>='2026-07-03']
print('buys >=07-03:',len(recent))
recent_s=[r for r in sells if r['time']>='2026-07-03']
print('sells >=07-03:',len(recent_s))
# per bot recent buys
bc=Counter(r['bot_id'] for r in recent)
for k,v in bc.most_common(): print('  ',k,v)
