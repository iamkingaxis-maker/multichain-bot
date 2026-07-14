import json, statistics as st
d=json.load(open('_full_trades.json'))
# index buys by address in order
buys=[t for t in d if t['type']=='buy']
sells=[t for t in d if t['type']=='sell']
# build per-address list of buys sorted by time
from collections import defaultdict
buys_by_addr=defaultdict(list)
for b in buys:
    buys_by_addr[b['address']].append(b)
for a in buys_by_addr: buys_by_addr[a].sort(key=lambda x:x['time'])

def prior_buy(sell):
    cands=[b for b in buys_by_addr.get(sell['address'],[]) if b['time']<=sell['time']]
    return cands[-1] if cands else None

rows=[]
for s in sells:
    b=prior_buy(s)
    if not b: continue
    em=b.get('entry_meta',{})
    pnl=s.get('pnl_pct')
    if pnl is None: continue
    hold=s.get('hold_secs')
    # SCRUB trivial round trips
    if pnl>0 and hold is not None and hold<10: continue
    rows.append(dict(addr=s['address'], pair=b.get('pair_address'), token=s['token'],
        pnl=pnl, frac=s.get('sell_fraction',1.0) or 1.0,
        pc_h24=em.get('pc_h24'), pc_h6=em.get('pc_h6'),
        medbuy=em.get('median_buy_size_usd'),
        vol=em.get('token_volatility_h24_pct'),
        time=s['time'], bot=s.get('bot_id')))
print('rows(sells realized, scrubbed):', len(rows))
print('distinct tokens:', len(set(r['addr'] for r in rows)))

def mult(pc_h24,pc_h6):
    try: h24=float(pc_h24)
    except: return 1.0
    if h24!=h24: return 1.0
    if h24<80: return 1.0
    try: h6=float(pc_h6); deep=(h6==h6) and h6<=-40
    except: deep=False
    return 0.70 if deep else 0.45

# classify
for r in rows:
    r['m']=mult(r['pc_h24'],r['pc_h6'])
    r['w']=r['frac']  # weight per realized fraction

def cvar(vals, q=0.05):
    v=sorted(vals); k=max(1,int(len(v)*q)); return sum(v[:k])/k

# UNIFORM: dollar hit = w*1.0*pnl ; DOWNSIZE: w*m*pnl
uni_dollar=[r['w']*r['pnl'] for r in rows]
dn_dollar=[r['w']*r['m']*r['pnl'] for r in rows]
uni_cap=sum(r['w']*1.0 for r in rows)
dn_cap=sum(r['w']*r['m'] for r in rows)
print('--- POOLED ---')
print('n rows', len(rows), 'downsized rows', sum(1 for r in rows if r['m']<1.0),
      'frac downsized', round(sum(1 for r in rows if r['m']<1.0)/len(rows),3))
print('distinct downsized tokens', len(set(r['addr'] for r in rows if r['m']<1.0)))
print('ROC/$ uniform', round(sum(uni_dollar)/uni_cap,4), ' downsize', round(sum(dn_dollar)/dn_cap,4))
print('CVaR5 uniform', round(cvar(uni_dollar),3), ' downsize', round(cvar(dn_dollar),3))
print('worst uniform', round(min(uni_dollar),3), ' downsize', round(min(dn_dollar),3))

# The downsized cohort itself
ds=[r for r in rows if r['m']<1.0]
sh=[r for r in ds if r['m']==0.45]  # violent shallow
dp=[r for r in ds if r['m']==0.70]  # violent deep
print('--- COHORTS ---')
for name,g in [('violent_shallow(0.45)',sh),('violent_deep(0.70)',dp)]:
    if not g: print(name,'EMPTY'); continue
    pn=[r['pnl'] for r in g]
    print(name,'n',len(g),'distinct',len(set(r['addr'] for r in g)),
          'mean',round(st.mean(pn),2),'median',round(st.median(pn),2),
          'p5',round(sorted(pn)[max(0,int(len(pn)*.05))],1),
          'p95',round(sorted(pn)[min(len(pn)-1,int(len(pn)*.95))],1),
          'std',round(st.pstdev(pn),1) if len(pn)>1 else 0)
