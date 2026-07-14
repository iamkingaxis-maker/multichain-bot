import json, numbers
from collections import defaultdict
import statistics

with open('_full_trades.json', encoding='utf-8') as f:
    d = json.load(f)
if isinstance(d, dict): d = d.get('trades', [])

buys=[r for r in d if r.get('type')=='buy' and (r.get('bot_id') or '').startswith('badday_')]
sells=[r for r in d if r.get('type')=='sell' and (r.get('bot_id') or '').startswith('badday_')]

sidx=defaultdict(list)
for s in sells:
    key=(s.get('bot_id'), s.get('address') or s.get('token'))
    sidx[key].append(s)
for k in sidx: sidx[k].sort(key=lambda x:x.get('time') or 0)

joined=[]
for b in buys:
    key=(b.get('bot_id'), b.get('address') or b.get('token'))
    bt=b.get('time') or 0
    cand=[s for s in sidx.get(key,[]) if (s.get('time') or 0)>=bt]
    if not cand: continue
    joined.append((b,cand[0]))

# Label + cohort
rows=[]  # dict: feats(em), label, token, time
for b,s in joined:
    pnl=s.get('pnl_pct')
    peak=s.get('peak_pnl_pct')
    if peak is None: continue
    if pnl is not None and abs(pnl)>150: continue  # phantom
    em=b.get('entry_meta') or {}
    # cohort gates
    cr3=em.get('1m_consec_red')
    pc_h6=em.get('pc_h6')
    liq=em.get('liquidity_usd')
    if cr3 is None or liq is None: continue
    if pc_h6 is None: pc_h6_ok=False
    else: pc_h6_ok = pc_h6>=0
    if not (cr3<3 and (pc_h6_ok or liq>=48000)): continue
    # label
    if peak<2: lab='NG'
    elif peak>=5: lab='B'
    else: continue
    rows.append({'em':em,'lab':lab,'token':b.get('address') or b.get('token'),
                 'tok_sym':b.get('token'),'time':b.get('time') or 0})

ng=[r for r in rows if r['lab']=='NG']
bb=[r for r in rows if r['lab']=='B']
print('cohort total', len(rows), 'NG', len(ng), 'B', len(bb))
print('base nevergreen rate %', round(100*len(ng)/len(rows),1))
print('distinct tokens', len(set(r['token'] for r in rows)))

# numeric feature universe
EXCLUDE_SUBSTR=['consec_red','liquidity_usd','pc_h6','1m_consec','signal_ts']
def is_num(v): return isinstance(v,numbers.Number) and not isinstance(v,bool)
allkeys=set()
for r in rows:
    for k,v in r['em'].items():
        if is_num(v): allkeys.add(k)
# exclude cr3/liq/pc_h6 re-derivations
feats=[k for k in allkeys if not any(x in k for x in EXCLUDE_SUBSTR)]
# also explicitly drop pc_h6 exact and liquidity
feats=[k for k in feats if k not in ('pc_h6','liquidity_usd')]
print('candidate numeric feats', len(feats))

import json as J
J.dump([{'em':{k:r['em'][k] for k in r['em'] if is_num(r['em'][k])},'lab':r['lab'],'token':r['token'],'time':r['time'],'sym':r['tok_sym']} for r in rows], open('scratch_rows.json','w'))
J.dump(feats, open('scratch_feats.json','w'))
