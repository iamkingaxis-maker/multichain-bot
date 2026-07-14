import json
from collections import defaultdict, Counter
import statistics as st
d=json.load(open('_full_trades.json'))
pending=defaultdict(list); pairs=[]
for t in d:
    key=(t.get('bot_id'), t.get('token'))
    if t.get('type')=='buy': pending[key].append(t)
    elif t.get('type')=='sell' and pending[key]:
        b=pending[key].pop(0); pairs.append((b,t))
pairs=[(b,s) for b,s in pairs if not (s.get('pnl_pct',0)>0 and s.get('hold_secs',999)<10)]
pairs=[(b,s) for b,s in pairs if s.get('pnl_pct') is not None]
def em(b,k): return (b.get('entry_meta') or {}).get(k)

print('=== nf15 IMBALANCE magnitude buckets (net_flow_60s_imbalance & 15s usd) ===')
def bkt(b,s):
    v=em(b,'net_flow_15s_usd')
    if v is None: return None
    if v<-50: return '0_<-50'
    if v<0: return '1_-50..0'
    if v<50: return '2_0..50'
    if v<200: return '3_50..200'
    return '4_>200'
g=defaultdict(list); gt=defaultdict(set)
for b,s in pairs:
    k=bkt(b,s)
    if k: g[k].append(s['pnl_pct']); gt[k].add(b['token'])
for k in sorted(g): print('  %-12s n=%4d ntok=%3d mean=%6.2f med=%6.2f'%(k,len(g[k]),len(gt[k]),st.mean(g[k]),st.median(g[k])))

print('\n=== net_flow_60s sign (broader fresh window) ===')
g=defaultdict(list); gt=defaultdict(set)
for b,s in pairs:
    v=em(b,'net_flow_60s_usd')
    if v is None: continue
    k='60s_pos' if v>0 else '60s_neg'
    g[k].append(s['pnl_pct']); gt[k].add(b['token'])
for k in sorted(g): print('  %-12s n=%4d ntok=%3d mean=%6.2f'%(k,len(g[k]),len(gt[k]),st.mean(g[k])))

print('\n=== COMBINED: fresh-turn (nf15>0 AND nf60>0) vs rest ===')
g=defaultdict(list); gt=defaultdict(set)
for b,s in pairs:
    a=em(b,'net_flow_15s_usd'); c=em(b,'net_flow_60s_usd')
    if a is None or c is None: continue
    k='fresh_turn_up' if (a>0 and c>0) else 'not_fresh'
    g[k].append(s['pnl_pct']); gt[k].add(b['token'])
for k in sorted(g): print('  %-14s n=%4d ntok=%3d mean=%6.2f med=%6.2f'%(k,len(g[k]),len(gt[k]),st.mean(g[k]),st.median(g[k])))
