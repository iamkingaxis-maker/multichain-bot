import json, statistics as st
from collections import defaultdict, Counter
d=json.load(open('_trades_fresh.json'))
buys=[x for x in d if x.get('type')=='buy']
sells=[x for x in d if x.get('type')=='sell']
# index buys by (address,bot_id)
bidx=defaultdict(list)
for b in buys:
    bidx[(b.get('address'),b.get('bot_id'))].append(b)
def parse(t):
    return t or ''
rows=[]
unmatched=0
for s in sells:
    key=(s.get('address'),s.get('bot_id'))
    cands=bidx.get(key,[])
    ep=s.get('entry_price')
    best=None;bd=1e9
    for b in cands:
        bep=b.get('entry_price')
        if bep and ep:
            diff=abs(bep-ep)/ep
            if diff<bd and b.get('time','')<=s.get('time',''):
                bd=diff;best=b
    if best is None or bd>0.01:
        unmatched+=1; continue
    em=best.get('entry_meta') or {}
    rows.append({
        'addr':s.get('address'),'sym':s.get('token'),'bot':s.get('bot_id'),
        'pnl':s.get('pnl_pct'),'mae':s.get('mae_pct'),'peak':s.get('peak_pnl_pct'),
        'hold':s.get('hold_secs'),'reason':s.get('reason'),
        'liq':em.get('liquidity_usd'),'pc_h6':em.get('pc_h6'),'pc_h24':em.get('pc_h24'),
        'pc_h1':em.get('pc_h1'),'pc_m5':em.get('pc_m5'),
        'age':em.get('lifecycle_age_hours'),'stage':em.get('lifecycle_stage'),
        'medbuy':em.get('median_buy_size_usd'),'ubuyers':em.get('unique_buyers_n'),
        'hl':em.get('hl_confirm_state'),
        'ws60':em.get('ws_pc_60s'),'ws30':em.get('ws_pc_30s'),
        'maxdrop1m':em.get('1m_max_drop'),'peakh24_6h':em.get('peak_h24_6h_pct'),
        'time':s.get('time'),'live':('live' in (s.get('bot_id') or '')),
        'tsl':em.get('trend_15m_slope_pct_per_min'),
        'ddown30':em.get('shape_30m_drawdown_from_max_pct'),
    })
print('matched',len(rows),'unmatched',unmatched)
json.dump(rows,open('_catch_rows.json','w'))
# scrub trivial round trips
scr=[r for r in rows if not (r['pnl'] is not None and r['pnl']>0 and r['hold'] is not None and r['hold']<10)]
print('after scrub',len(scr))
# basic distributions
def q(xs,p):
    xs=sorted([x for x in xs if x is not None]); 
    if not xs:return None
    i=min(len(xs)-1,int(p*len(xs))); return xs[i]
pnls=[r['pnl'] for r in scr if r['pnl'] is not None]
maes=[r['mae'] for r in scr if r['mae'] is not None]
print('PNL med',round(st.median(pnls),2),'p10',round(q(pnls,.1),2),'p90',round(q(pnls,.9),2),'n',len(pnls))
print('MAE med',round(st.median(maes),2),'p90',round(q(maes,.9),2),'p95',round(q(maes,.95),2))
print('liq present', sum(1 for r in scr if r['liq'] is not None))
print('pc_h6 present', sum(1 for r in scr if r['pc_h6'] is not None))
print('age present', sum(1 for r in scr if r['age'] is not None))
