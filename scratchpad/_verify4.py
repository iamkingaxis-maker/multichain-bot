import json, statistics as st
from collections import defaultdict
d=json.load(open('_full_trades.json'))
buys=[t for t in d if t['type']=='buy']; sells=[t for t in d if t['type']=='sell']
ba=defaultdict(list)
for b in buys: ba[b['address']].append(b)
for a in ba: ba[a].sort(key=lambda x:x['time'])
def pb(s):
    c=[b for b in ba.get(s['address'],[]) if b['time']<=s['time']]; return c[-1] if c else None
pos=defaultdict(lambda:{'buy':None,'rpnl':0.0,'fsum':0.0})
for s in sells:
    b=pb(s)
    if not b: continue
    pnl=s.get('pnl_pct')
    if pnl is None: continue
    h=s.get('hold_secs')
    if pnl>0 and h is not None and h<10: continue
    k=id(b); pos[k]['buy']=b
    f=s.get('sell_fraction',1.0) or 1.0
    if pos[k]['fsum']+f>1: f=max(0,1-pos[k]['fsum'])
    pos[k]['rpnl']+=f*pnl; pos[k]['fsum']+=f
rows=[]
for k,p in pos.items():
    if p['fsum']<=0: continue
    b=p['buy']; em=b.get('entry_meta',{})
    rows.append(dict(addr=b['address'],pair=b.get('pair_address'),rpnl=p['rpnl'],
        pc_h24=em.get('pc_h24'),pc_h6=em.get('pc_h6'),time=b['time']))
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
def roc(seg): return sum(r['m']*r['rpnl'] for r in seg)/sum(r['m'] for r in seg)
def rocfull(seg): return sum(r['rpnl'] for r in seg)/len(seg)
rows.sort(key=lambda r:r['time'])
print('OUT-OF-SAMPLE time split (capped position method):')
mid=len(rows)//2
for lab,seg in [('EARLY',rows[:mid]),('LATE',rows[mid:])]:
    u=[r['rpnl'] for r in seg]; dn=[r['m']*r['rpnl'] for r in seg]
    kf=sum(r['m'] for r in seg)/len(seg)
    print(f' {lab} n{len(seg)} distTok{len(set(r["addr"] for r in seg))}: '
      f'ROCfull {rocfull(seg):.3f} lever {roc(seg):.3f} | '
      f'CVaR full {cvar(u):.2f} lever {cvar(dn):.2f} unifShrink {cvar([kf*x for x in u]):.2f}')
# fat-tail dependence: drop top-3 right-tail positions in each violent cohort, recheck EV
print('\nFat-tail dependence (viol cohorts):')
for nm,mv in [('viol_shallow',0.45),('viol_deep',0.70)]:
    g=sorted([r['rpnl'] for r in rows if r['m']==mv])
    if not g: continue
    print(f' {nm}: n{len(g)} mean {st.mean(g):.2f} | drop-top3 mean {st.mean(g[:-3]):.2f} | median {st.median(g):.2f} | %>0 {100*sum(1 for x in g if x>0)/len(g):.0f}')
# per-pair robustness: sign of (lever - full) contribution per pair? 
# Simpler: does downsizing win in BOTH regime halves? already above.
# Whole-set headline reproduce:
u=[r['rpnl'] for r in rows]; dn=[r['m']*r['rpnl'] for r in rows]
print(f'\nWHOLE: ROCfull {rocfull(rows):.3f} lever {roc(rows):.3f} | CVaR full {cvar(u):.2f} lever {cvar(dn):.2f} | worst {min(u):.1f}->{min(dn):.1f}')
print('deployed capital: full',len(rows),'lever',round(sum(r["m"] for r in rows),0))
