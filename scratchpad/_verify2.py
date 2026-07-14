import json, statistics as st
from collections import defaultdict
d=json.load(open('_full_trades.json'))
buys=[t for t in d if t['type']=='buy']
sells=[t for t in d if t['type']=='sell']
buys_by_addr=defaultdict(list)
for b in buys: buys_by_addr[b['address']].append(b)
for a in buys_by_addr: buys_by_addr[a].sort(key=lambda x:x['time'])
def prior_buy(sell):
    c=[b for b in buys_by_addr.get(sell['address'],[]) if b['time']<=sell['time']]
    return c[-1] if c else None
# aggregate sells to their buy (position = identity of buy dict)
pos=defaultdict(lambda: {'buy':None,'rpnl':0.0,'fsum':0.0,'sells':[]})
for s in sells:
    b=prior_buy(s)
    if not b: continue
    pnl=s.get('pnl_pct'); 
    if pnl is None: continue
    hold=s.get('hold_secs')
    if pnl>0 and hold is not None and hold<10: continue  # scrub
    key=id(b)
    pos[key]['buy']=b
    f=s.get('sell_fraction',1.0) or 1.0
    pos[key]['rpnl']+=f*pnl
    pos[key]['fsum']+=f
    pos[key]['sells'].append(s)
rows=[]
for k,p in pos.items():
    b=p['buy']; em=b.get('entry_meta',{})
    if p['fsum']<=0: continue
    rows.append(dict(addr=b['address'],pair=b.get('pair_address'),token=b['token'],
        rpnl=p['rpnl'], fsum=p['fsum'],
        pc_h24=em.get('pc_h24'),pc_h6=em.get('pc_h6'),
        medbuy=em.get('median_buy_size_usd'),vol=em.get('token_volatility_h24_pct'),
        time=b['time'],bot=b.get('bot_id')))
print('positions:',len(rows),'distinct tokens:',len(set(r['addr'] for r in rows)))
def mult(h24,h6):
    try: x=float(h24)
    except: return 1.0
    if x!=x or x<80: return 1.0
    try: y=float(h6); deep=(y==y) and y<=-40
    except: deep=False
    return 0.70 if deep else 0.45
for r in rows: r['m']=mult(r['pc_h24'],r['pc_h6'])
def cvar(v,q=0.05):
    v=sorted(v); k=max(1,int(len(v)*q)); return sum(v[:k])/k
uni=[r['rpnl'] for r in rows]; dn=[r['m']*r['rpnl'] for r in rows]
ucap=len(rows); dcap=sum(r['m'] for r in rows)
print('--- POSITION-LEVEL POOLED ---')
print('frac downsized',round(sum(1 for r in rows if r['m']<1)/len(rows),3),
      'distinct downsized',len(set(r['addr'] for r in rows if r['m']<1)))
print('ROC/$ uni',round(sum(uni)/ucap,4),'dn',round(sum(dn)/dcap,4))
print('CVaR5 uni',round(cvar(uni),3),'dn',round(cvar(dn),3))
print('worst uni',round(min(uni),2),'dn',round(min(dn),2))
# cohort means position-level
for nm,mv in [('viol_shallow',0.45),('viol_deep',0.70)]:
    g=[r['rpnl'] for r in rows if r['m']==mv]
    if g: print(nm,'n',len(g),'distinct',len(set(r['addr'] for r in rows if r['m']==mv)),
        'mean',round(st.mean(g),2),'median',round(st.median(g),2))
calm=[r['rpnl'] for r in rows if r['m']==1.0]
print('calm n',len(calm),'mean',round(st.mean(calm),2),'median',round(st.median(calm),2))

# OUT-OF-SAMPLE: split by time (median)
rows.sort(key=lambda r:r['time'])
mid=len(rows)//2
for lab,seg in [('EARLY half',rows[:mid]),('LATE half',rows[mid:])]:
    u=[r['rpnl'] for r in seg]; dd=[r['m']*r['rpnl'] for r in seg]
    dc=sum(r['m'] for r in seg)
    print(f'{lab}: n{len(seg)} ROC uni',round(sum(u)/len(seg),3),'dn',round(sum(dd)/dc,3),
          'CVaR uni',round(cvar(u),2),'dn',round(cvar(dd),2),
          'downsized frac',round(sum(1 for r in seg if r['m']<1)/len(seg),2))
