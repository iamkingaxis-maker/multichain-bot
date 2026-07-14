import json
from collections import defaultdict
from datetime import datetime
exec(open('scratchpad/_rh_rug_analyze.py').read().split('closed=[t')[0])  # reuse loaders+trips
# rebuild trips (the analyze file already built them up to 'trips')
# Re-run the trip build block:
bykey=defaultdict(list)
for r in rows:
    if r['ev'] in ('buy','sell'):
        bykey[(r.get('bot_id'), r['pool'])].append(r)
trips=[]
for key, evs in bykey.items():
    evs.sort(key=lambda r: pe(r['ts']))
    cur=None
    for r in evs:
        if r['ev']=='buy':
            if cur is not None: trips.append(cur)
            cur={'bot_id':key[0],'pool':key[1],'sym':r.get('sym'),'token':r.get('token'),
                 'entry_ts':pe(r['ts']),'entry_usd':r.get('usd',25.0),'dip':r.get('dip_pct'),
                 'liq':r.get('liq'),'sells':[],'closed':False}
        elif r['ev']=='sell' and cur is not None:
            cur['sells'].append(r)
            if r.get('fully'):
                cur['closed']=True; trips.append(cur); cur=None
    if cur is not None: trips.append(cur)
for t in trips:
    pnl=sum(s.get('pnl_usd',0.0) for s in t['sells'])
    t['ret_pct']=pnl/t['entry_usd']*100.0 if t['entry_usd'] else 0.0
closed=[t for t in trips if t['closed']]

# rug_signals stamps keyed by pool (take first non-cached / best-completeness stamp per pool)
stamp_by_pool={}
for r in rug:
    p=r['pool']
    # prefer a stamp that reached genesis (replay_supply_match) and isn't truncated
    prev=stamp_by_pool.get(p)
    def score(s): return (1 if s.get('replay_supply_match') else 0, 0 if s.get('truncated') else 1, -(s.get('cost',{}).get('secs',999)))
    if prev is None or score(r)>score(prev):
        stamp_by_pool[p]=r
print('rug_signals stamps: pools covered', len(stamp_by_pool))

# token-level: worst realized ret and whether stamped
tok_worst=defaultdict(lambda:1e9); tok_sym={}; tok_pool={}
for t in closed:
    if t['ret_pct']<tok_worst[t['token']]:
        tok_worst[t['token']]=t['ret_pct']
    tok_sym[t['token']]=t['sym']; tok_pool[t['token']]=t['pool']

# label: RUG if worst trip <= -30
def feats(pool):
    s=stamp_by_pool.get(pool)
    if not s: return None
    return s

print('\n%-13s %7s %6s %5s %5s %6s %6s %6s %6s %5s %s'%('sym','worst%','pool%','top1','top10','shldr','float','sh/t10','nhold','trunc','stamp?'))
rows_out=[]
for tok,worst in sorted(tok_worst.items(), key=lambda x:x[1]):
    s=feats(tok_pool[tok])
    label='RUG' if worst<=-30 else ('LOSS' if worst<=-20 else 'ok')
    if s:
        rows_out.append((tok_sym[tok],worst,s,label))
        print('%-13s %7.1f %6s %5s %5s %6s %6s %6s %6s %5s %s'%(
            tok_sym[tok],worst,s.get('pool_pct_of_supply'),s.get('top1_pct'),s.get('top10_pct'),
            s.get('shoulder_11_20_pct'),s.get('visible_float_pct'),s.get('shoulder_to_top10_ratio'),
            s.get('n_holders'),s.get('truncated'),label))
    else:
        print('%-13s %7.1f %6s (NO STAMP) %s'%(tok_sym[tok],worst,'',label))

# save joined for gate design
import pickle
pickle.dump({'closed':closed,'stamp_by_pool':stamp_by_pool,'tok_worst':dict(tok_worst),
             'tok_sym':dict(tok_sym),'tok_pool':dict(tok_pool)}, open('scratchpad/_rh_rug_joined.pkl','wb'))
