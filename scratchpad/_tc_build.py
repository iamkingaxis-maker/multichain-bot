import json, statistics as st
from collections import defaultdict
d=json.load(open('scratchpad/_tcond_trades.json'))
buys=[t for t in d if t.get('type')=='buy']
sells=[t for t in d if t.get('type')=='sell']
# index buys by (address, entry_price rounded)
def key(t): return (t.get('address'), round(t.get('entry_price') or 0,14))
bidx={}
for b in buys:
    bidx.setdefault(key(b),[]).append(b)
# group sells by same key -> aggregate a position
sg=defaultdict(list)
for s in sells: sg[key(s)].append(s)
print('buy keys',len(bidx),'sell-group keys',len(sg))
matched=0
positions=[]
for k,ss in sg.items():
    b=bidx.get(k)
    if not b: continue
    b=b[0]
    matched+=1
    em=b.get('entry_meta') or {}
    # position realized pnl = sell_fraction-weighted pnl_pct
    fracs=[s.get('sell_fraction') or 0 for s in ss]
    pnls=[s.get('pnl_pct') for s in ss if s.get('pnl_pct') is not None]
    tw=sum(f for f,s in zip(fracs,ss) if s.get('pnl_pct') is not None)
    if tw>0:
        wp=sum((s.get('sell_fraction') or 0)*s.get('pnl_pct') for s in ss if s.get('pnl_pct') is not None)/tw
    else:
        wp=st.mean(pnls) if pnls else None
    maes=[s.get('mae_pct') for s in ss if s.get('mae_pct') is not None]
    mae=min(maes) if maes else None
    holds=[s.get('hold_secs') for s in ss if s.get('hold_secs') is not None]
    hold=max(holds) if holds else None
    positions.append(dict(addr=k[0],token=b.get('token'),bot=b.get('bot_id'),
        entry_price=b.get('entry_price'),amount_usd=b.get('amount_usd'),
        pnl=wp,mae=mae,hold=hold,em=em,time=b.get('time')))
print('matched positions',matched)
# scrub: drop pnl>0 & hold<10
before=len(positions)
positions=[p for p in positions if not (p['pnl'] is not None and p['pnl']>0 and p['hold'] is not None and p['hold']<10)]
print('after scrub',len(positions),'dropped',before-len(positions))
# require pnl and em non-empty
positions=[p for p in positions if p['pnl'] is not None and p['em']]
print('with pnl+em',len(positions))
json.dump(positions,open('scratchpad/_tc_positions.json','w'))
# outcome distributions
pn=[p['pnl'] for p in positions]
print('pnl median %.2f mean %.2f WR %.1f%%'%(st.median(pn),st.mean(pn),100*sum(1 for x in pn if x>0)/len(pn)))
mae=[p['mae'] for p in positions if p['mae'] is not None]
print('mae n=%d median %.2f p10 %.2f min %.2f'%(len(mae),st.median(mae),sorted(mae)[len(mae)//10],min(mae)))
# gap-through count: mae < -15 (below a -7 stop meaningfully)
gt=[p for p in positions if p['mae'] is not None and p['mae']<-15]
print('gap-through(mae<-15):',len(gt), '%.1f%%'%(100*len(gt)/len(mae)))
