import json, statistics
from collections import defaultdict
d=json.load(open('_vf_trades.json'))
buys=[t for t in d if t.get('type')=='buy']
sells=[t for t in d if t.get('type')=='sell']
# index sells by (bot_id,address,entry_price)
sidx=defaultdict(list)
for s in sells:
    k=(s.get('bot_id'),s.get('address'),round(s.get('entry_price') or 0,14))
    sidx[k].append(s)
rows=[]
matched=0
for b in buys:
    em=b.get('entry_meta') or {}
    bsl=em.get('1s_bars_since_low_60s')
    k=(b.get('bot_id'),b.get('address'),round(b.get('entry_price') or 0,14))
    ss=sidx.get(k)
    if not ss: continue
    matched+=1
    # aggregate realized: sum pnl weighted by sell_fraction if available; else take fully_closed one
    # Use volume-weighted pnl_pct across sell fractions
    tot_frac=sum((s.get('sell_fraction') or 0) for s in ss)
    if tot_frac>0:
        wp=sum((s.get('pnl_pct') or 0)*(s.get('sell_fraction') or 0) for s in ss)/tot_frac
    else:
        wp=statistics.mean([s.get('pnl_pct') or 0 for s in ss])
    hold=max((s.get('hold_secs') or 0) for s in ss)
    rows.append(dict(bot=b.get('bot_id'),addr=b.get('address'),pair=b.get('pair_address'),
        bsl=bsl, close_pos=em.get('1s_close_pos_60s'),
        ub=em.get('unique_buyers_n'), nf15=em.get('net_flow_15s_usd'),
        hl=em.get('hl_confirm_state'), pnl=wp, hold=hold,
        entry_slip=b.get('entry_slip_pct'), token=b.get('token'),
        time=b.get('time'), live=('shadow' not in (b.get('bot_id') or ''))))
json.dump(rows,open('_vf_rows.json','w'))
print('buys',len(buys),'sells',len(sells),'matched',matched,'rows',len(rows))
bsl_known=[r for r in rows if r['bsl'] is not None]
print('bsl known',len(bsl_known))
