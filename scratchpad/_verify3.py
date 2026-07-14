import json, statistics as st
from collections import defaultdict
d=json.load(open('_full_trades.json'))
buys=[t for t in d if t['type']=='buy']; sells=[t for t in d if t['type']=='sell']
buys_by_addr=defaultdict(list)
for b in buys: buys_by_addr[b['address']].append(b)
for a in buys_by_addr: buys_by_addr[a].sort(key=lambda x:x['time'])
def prior_buy(s):
    c=[b for b in buys_by_addr.get(s['address'],[]) if b['time']<=s['time']]
    return c[-1] if c else None
pos=defaultdict(lambda:{'buy':None,'rpnl':0.0,'fsum':0.0})
for s in sells:
    b=prior_buy(s); 
    if not b: continue
    pnl=s.get('pnl_pct')
    if pnl is None: continue
    h=s.get('hold_secs')
    if pnl>0 and h is not None and h<10: continue
    k=id(b); pos[k]['buy']=b
    f=s.get('sell_fraction',1.0) or 1.0
    if pos[k]['fsum']+f>1.0: f=max(0.0,1.0-pos[k]['fsum'])  # cap total fraction at 1
    pos[k]['rpnl']+=f*pnl; pos[k]['fsum']+=f
rows=[]
for k,p in pos.items():
    b=p['buy']; em=b.get('entry_meta',{})
    if p['fsum']<=0: continue
    rows.append(dict(addr=b['address'],pair=b.get('pair_address'),
        rpnl=p['rpnl'],pc_h24=em.get('pc_h24'),pc_h6=em.get('pc_h6'),
        medbuy=em.get('median_buy_size_usd')))
def mult(h24,h6):
    try: x=float(h24)
    except: return 1.0
    if x!=x or x<80: return 1.0
    try: y=float(h6); deep=(y==y) and y<=-40
    except: deep=False
    return 0.70 if deep else 0.45
for r in rows: r['m']=mult(r['pc_h24'],r['pc_h6'])
def cvar(v,q=0.05):
    v=sorted(v);k=max(1,int(len(v)*q));return sum(v[:k])/k
uni=[r['rpnl'] for r in rows]; dn=[r['m']*r['rpnl'] for r in rows]
print('positions',len(rows),'worst',round(min(uni),1))
print('ROC/$ uniform-full',round(sum(uni)/len(rows),4))
print('ROC/$ lever(selective)',round(sum(dn)/sum(r['m'] for r in rows),4))
# uniform shrink to SAME total capital as lever -> ROC/$ identical to full, tail scales by k
kcap=sum(r['m'] for r in rows)/len(rows)  # avg multiplier
print('uniform-shrink-all factor',round(kcap,3),
      '-> ROC/$',round(sum(uni)/len(rows),4),'(unchanged), CVaR5',round(cvar([kcap*x for x in uni]),2))
print('lever CVaR5',round(cvar(dn),2),' full CVaR5',round(cvar(uni),2))
# cohort EV ordering (equal-weight positions, and distinct-token median)
def tok_med(mv):
    byt=defaultdict(list)
    for r in rows:
        if r['m']==mv: byt[r['addr']].append(r['rpnl'])
    meds=[st.mean(v) for v in byt.values()]
    return len(byt),(round(st.median(meds),2) if meds else None)
for nm,mv in [('viol_shallow(0.45)',0.45),('viol_deep(0.70)',0.70),('calm(1.0)',1.0)]:
    g=[r['rpnl'] for r in rows if r['m']==mv]
    nt,tm=tok_med(mv)
    print(nm,'pos',len(g),'distinct',nt,'posMean',round(st.mean(g),2),'tokMedian',tm)

# UPSIZE cell: deep-flush pc_h6<=-25 AND big-buyer medbuy>=34
def deepbig(r):
    try: h6=float(r['pc_h6']); mb=float(r['medbuy'])
    except: return False
    return h6==h6 and mb==mb and h6<=-25 and mb>=34
cell=[r['rpnl'] for r in rows if deepbig(r)]
rest=[r['rpnl'] for r in rows if not deepbig(r)]
ct=len(set(r['addr'] for r in rows if deepbig(r)))
print('UPSIZE cell(deep<=-25 & medbuy>=34): pos',len(cell),'distinct',ct,
      'mean',round(st.mean(cell),2) if cell else None,'vs rest mean',round(st.mean(rest),2))
# per-pair robustness of downsized cohort: fraction of pairs where viol mean < calm mean
