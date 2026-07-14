import json
from collections import defaultdict, Counter
import statistics as st
d=json.load(open('_full_trades.json'))
# order preserved. join sell to most recent prior buy same token+bot
buys=[t for t in d if t.get('type')=='buy']
# build per (bot,token) stack of buys in order
pending=defaultdict(list)
pairs=[]  # (buy, sell)
for t in d:
    key=(t.get('bot_id'), t.get('token'))
    if t.get('type')=='buy':
        pending[key].append(t)
    elif t.get('type')=='sell':
        if pending[key]:
            b=pending[key].pop(0)
            pairs.append((b,t))
print('joined pairs', len(pairs))
# scrub trivial round trips: drop ret>0 & hold<10s
def scrub(b,s):
    p=s.get('pnl_pct'); h=s.get('hold_secs')
    if p is None or h is None: return True
    if p>0 and h<10: return False
    return True
pairs=[(b,s) for b,s in pairs if scrub(b,s)]
print('after scrub', len(pairs))
# distinct tokens
toks=set(b.get('token') for b,s in pairs)
print('distinct tokens', len(toks))
# overall realized
pnls=[s['pnl_pct'] for b,s in pairs if s.get('pnl_pct') is not None]
print('mean pnl_pct %.3f median %.3f n=%d'%(st.mean(pnls),st.median(pnls),len(pnls)))

def em(b,k): return (b.get('entry_meta') or {}).get(k)
def grp_stats(pairs, keyfn):
    g=defaultdict(list); gt=defaultdict(set)
    for b,s in pairs:
        v=keyfn(b,s)
        if v is None: continue
        if s.get('pnl_pct') is None: continue
        g[v].append(s['pnl_pct']); gt[v].add(b.get('token'))
    for k in sorted(g, key=lambda x:(str(type(x)),x) if not isinstance(x,bool) else (str(x))):
        vs=g[k]
        print('  %-22s n=%4d ntok=%3d mean=%7.2f med=%7.2f'%(str(k),len(vs),len(gt[k]),st.mean(vs),st.median(vs)))

print('\n=== time_since_local_low_s buckets (how late past the local low we filled) ===')
def tslbucket(b,s):
    v=em(b,'time_since_local_low_s')
    if v is None: return None
    if v<=15: return '0_<=15s'
    if v<=45: return '1_15-45s'
    if v<=120: return '2_45-120s'
    if v<=300: return '3_120-300s'
    return '4_>300s'
grp_stats(pairs, tslbucket)

print('\n=== net_flow freshness: sign of 15s vs 5m imbalance ===')
def nf(b,s):
    f15=em(b,'net_flow_15s_usd'); f5=em(b,'net_flow_5m_usd')
    if f15 is None or f5 is None: return None
    a='+' if f15>0 else '-'
    c='+' if f5>0 else '-'
    return 'nf15%s_nf5m%s'%(a,c)
grp_stats(pairs, nf)

print('\n=== rt_secs_since_last (staleness of last RT trade at entry) ===')
def rtstale(b,s):
    v=em(b,'rt_secs_since_last')
    if v is None: return None
    if v<=2: return '0_<=2s'
    if v<=5: return '1_2-5s'
    if v<=15: return '2_5-15s'
    return '3_>15s'
grp_stats(pairs, rtstale)

print('\n=== FRESH (nf15 sign) vs WINDOWED (nf5m sign) main effects ===')
def sign15(b,s):
    v=em(b,'net_flow_15s_usd'); 
    return None if v is None else ('nf15_pos' if v>0 else 'nf15_neg')
def sign5m(b,s):
    v=em(b,'net_flow_5m_usd'); 
    return None if v is None else ('nf5m_pos' if v>0 else 'nf5m_neg')
grp_stats(pairs, sign15)
grp_stats(pairs, sign5m)

print('\n=== nf15 sign PER-BOT robustness (bots with n>=25) ===')
byb=defaultdict(lambda: defaultdict(list))
byt=defaultdict(lambda: defaultdict(set))
for b,s in pairs:
    v=em(b,'net_flow_15s_usd')
    if v is None or s.get('pnl_pct') is None: continue
    sg='pos' if v>0 else 'neg'
    byb[b['bot_id']][sg].append(s['pnl_pct'])
    byt[b['bot_id']][sg].add(b['token'])
rob=0; tot=0
for bot in sorted(byb):
    pos=byb[bot]['pos']; neg=byb[bot]['neg']
    if len(pos)>=8 and len(neg)>=8:
        tot+=1
        dif=st.mean(pos)-st.mean(neg)
        if dif>0: rob+=1
        print('  %-30s pos n=%3d(%2dt) m=%6.2f | neg n=%3d(%2dt) m=%6.2f | dpos=%6.2f'%(bot,len(pos),len(byt[bot]['pos']),st.mean(pos),len(neg),len(byt[bot]['neg']),st.mean(neg),dif))
print('  robust (pos>neg): %d/%d bots'%(rob,tot))

print('\n=== token-level: is nf15+_nf5m- cohort concentrated? top tokens by n ===')
c=Counter()
for b,s in pairs:
    f15=em(b,'net_flow_15s_usd'); f5=em(b,'net_flow_5m_usd')
    if f15 is not None and f5 is not None and f15>0 and f5<0:
        c[b['token'][:8]]+=1
print('  top', c.most_common(6), 'total tokens', len(c))
