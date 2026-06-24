import json
from collections import defaultdict
d=json.load(open('_full_trades.json')); t=d.get('trades',d) if isinstance(d,dict) else d
BADDAY={'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot=lambda x:x.get('bot_id') or x.get('strategy')
buys={(bot(x),(x.get('address')or x.get('token')or'').lower()):x for x in t if isinstance(x,dict) and x.get('type')=='buy' and bot(x) in BADDAY}
sells=[x for x in t if isinstance(x,dict) and x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float))]
tok=defaultdict(lambda:{'pnls':[],'em':None})
for s in sells:
    a=(s.get('address')or s.get('token')or'').lower()
    em=(buys.get((bot(s),a)) or {}).get('entry_meta') or {}
    tok[a]['pnls'].append(s.get('pnl_pct'))
    if em and tok[a]['em'] is None: tok[a]['em']=em
rows=[]
for a,v in tok.items():
    em=v['em'] or {}
    g=lambda k:em.get(k)
    rows.append((sum(v['pnls'])/len(v['pnls']), len(v['pnls']), a[:8],
        g('turnover_h24_ratio'),g('entry_volume_h24_usd'),g('liquidity_usd'),
        g('top10_holder_pct'),g('pc_h6'),g('lifecycle_age_hours'),g('unique_buyer_ratio')))
rows.sort()
print("worst 10 tokens (pnl, fills, addr, turnover, vol_h24, liq, top10, pch6, age, ubr):")
for r in rows[:10]:
    print(f"  {r[0]:7.1f} n={r[1]:2} {r[2]} turn={r[3]} vol={r[4]} liq={r[5]} top10={r[6]} pch6={r[7]} age={r[8]} ubr={r[9]}")
print("best 6 tokens:")
for r in rows[-6:]:
    print(f"  {r[0]:7.1f} n={r[1]:2} {r[2]} turn={r[3]} vol={r[4]} liq={r[5]} top10={r[6]} pch6={r[7]} age={r[8]} ubr={r[9]}")
