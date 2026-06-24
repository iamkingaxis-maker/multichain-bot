import json, statistics
from collections import defaultdict

d=json.load(open(r'C:\Users\jcole\multichain-bot\_wsig_full.json'))
t=d if isinstance(d,list) else d['trades']
gp=[x for x in t if x.get('bot_id')=='pool_a_goodpond']
def ts(x): return x['time']
buys=[x for x in gp if x['type']=='buy']
sells=[x for x in gp if x['type']=='sell']
sells_by_addr=defaultdict(list)
for s in sells: sells_by_addr[s['address']].append(s)
for a in sells_by_addr: sells_by_addr[a].sort(key=ts)

joined=[]
for b in sorted(buys,key=ts):
    cand=[s for s in sells_by_addr.get(b['address'],[]) if s['time']>b['time']]
    if cand: joined.append((b,cand[0]))

def pnl(s): return float(s['pnl_pct'])
rows=[(b,s,pnl(s)) for b,s in joined]
# drop phantoms
rows=[r for r in rows if abs(r[2])<=300]
winners=[r for r in rows if r[2]>0]
losers=[r for r in rows if r[2]<=0]
print('pairs',len(rows),'winners',len(winners),'losers',len(losers))
print('win pnls', sorted(round(r[2],2) for r in winners))
print('lose pnls', sorted(round(r[2],2) for r in losers))

# collect numeric features from entry_meta
def feats(b):
    em=b.get('entry_meta',{}) or {}
    out={}
    for k,v in em.items():
        if isinstance(v,bool): continue
        if isinstance(v,(int,float)): out[k]=float(v)
    return out

allkeys=set()
for b,s,p in rows: allkeys|=set(feats(b).keys())

HOLDER=lambda k: ('holder' in k.lower()) or k.lower().startswith('top10_holder') or k.endswith('_hf')

results=[]
n=len(rows)
for k in sorted(allkeys):
    wv=[feats(b).get(k) for b,s,p in winners if feats(b).get(k) is not None]
    lv=[feats(b).get(k) for b,s,p in losers if feats(b).get(k) is not None]
    nonnull=sum(1 for b,s,p in rows if feats(b).get(k) is not None)
    cov=100.0*nonnull/n
    if len(wv)<2 or len(lv)<2: continue
    wmed=statistics.median(wv); lmed=statistics.median(lv)
    # pooled std
    allv=wv+lv
    try: psd=statistics.pstdev(allv)
    except: psd=0
    if psd==0: sep=0
    else: sep=abs(wmed-lmed)/psd
    # default-0-when-missing risk: check if min over all == 0 and there are zeros
    zeros=sum(1 for v in allv if v==0)
    results.append((sep,k,wmed,lmed,cov,len(wv),len(lv),zeros,len(allv)))

results.sort(reverse=True)
print('\nTOP SEPARATORS (sep, key, wmed, lmed, cov%, nw, nl, zeros/total):')
for r in results[:35]:
    sep,k,wmed,lmed,cov,nw,nl,z,tot=r
    hold='HOLDER' if HOLDER(k) else ''
    print(f'{sep:5.2f} {k:42s} w={wmed:11.4g} l={lmed:11.4g} cov={cov:5.1f} nw={nw} nl={nl} z={z}/{tot} {hold}')

# also dump the recurring-winner features if present
recur=['net_flow_15s_imbalance','shape_90m_drawdown','shape_30m_drawdown','1s_bottom_score',
'consec_higher_lows_1m','5m_consec_green','chart_reaccum_drawdown_pct']
print('\nRECURRING-WINNER FEATURE PRESENCE:')
for rk in recur:
    present=[k for k in allkeys if rk in k or k in rk]
    print(f'  {rk}: keys={present}')
