import json, statistics
from collections import defaultdict
d=json.load(open('_df_full.json.gz'))
buys=[r for r in d if r.get('type')=='buy']
sells=[r for r in d if r.get('type')=='sell']

# buy lookup by (address, entry_price) -> entry_meta
buylk={}
dup=0
for b in buys:
    k=(b.get('address'), b.get('entry_price'))
    if k in buylk: dup+=1
    buylk[k]=b
print('buys',len(buys),'dup keys',dup)

# group sells
grp=defaultdict(list)
for s in sells:
    grp[(s.get('address'), s.get('entry_price'))].append(s)

# build positions: realized pnl_pct = fraction-weighted
positions=[]
matched=0
for k,legs in grp.items():
    b=buylk.get(k)
    if b is None: continue
    em=b.get('entry_meta') or {}
    if em.get('chart_mtf_score') is None: continue
    wsum=sum((l.get('sell_fraction') or 0) for l in legs)
    if wsum>0:
        pnl=sum((l.get('pnl_pct') or 0)*(l.get('sell_fraction') or 0) for l in legs)/wsum
    else:
        pnl=statistics.mean([l.get('pnl_pct') or 0 for l in legs])
    positions.append({'mtf':em['chart_mtf_score'],'pnl':pnl,'em':em})
    matched+=1
print('matched positions with mtf:', matched)

def bucket(m):
    if m>=-1 and m!=-1: # >=0 actually; spec: [>=-1 PASS-zone],[-1],[-2],[-3],[<=-4]
        pass
    return m
# Spec buckets: >=-1(pass-zone means >= -1? they list [-1] separately) -> interpret: >=0, -1, -2, -3, <=-4
def bname(m):
    if m>=0: return '>=0 (PASS-zone)'
    if m==-1: return '-1'
    if m==-2: return '-2'
    if m==-3: return '-3'
    return '<=-4'
order=['>=0 (PASS-zone)','-1','-2','-3','<=-4']
b=defaultdict(list)
for p in positions: b[bname(p['mtf'])].append(p['pnl'])

print()
print("{:18} {:>5} {:>8} {:>8} {:>6} {:>11}".format('bucket','n','mean','median','WR%','closed_red%'))
for nm in order:
    v=b[nm]
    if not v:
        print("{:18} {:>5}".format(nm,0)); continue
    n=len(v); mean=statistics.mean(v); med=statistics.median(v)
    wr=100*sum(1 for x in v if x>0)/n
    ng=100*sum(1 for x in v if x<=0)/n
    print("{:18} {:>5} {:>8.2f} {:>8.2f} {:>6.1f} {:>11.1f}".format(nm,n,mean,med,wr,ng))

from collections import Counter
print()
print('raw mtf value counts:', dict(sorted(Counter(p['mtf'] for p in positions).items())))

# Incremental block analysis: among mtf<=-2 (gate blocks), how many ALSO caught by falling_knife/consec_red>=3?
print()
print('Among mtf<=-2 positions (the gate blocks these):')
deep=[p for p in positions if p['mtf']<=-2]
def stat(v):
    if not v: return (0,0,0,0)
    n=len(v); return (n, statistics.mean(v), statistics.median(v), 100*sum(1 for x in v if x>0)/n)
fk=[p['pnl'] for p in deep if p['em'].get('filter_falling_knife_verdict')=='BLOCK']
nfk=[p['pnl'] for p in deep if p['em'].get('filter_falling_knife_verdict')!='BLOCK']
cr=[p['pnl'] for p in deep if (p['em'].get('1m_consec_red') or 0)>=3]
ncr=[p['pnl'] for p in deep if (p['em'].get('1m_consec_red') or 0)<3]
print('  falling_knife BLOCK:    n,mean,med,WR =', tuple(round(x,2) for x in stat(fk)))
print('  falling_knife notBLOCK: n,mean,med,WR =', tuple(round(x,2) for x in stat(nfk)))
print('  consec_red>=3:          n,mean,med,WR =', tuple(round(x,2) for x in stat(cr)))
print('  consec_red<3 (incrementally blocked only by mtf):', tuple(round(x,2) for x in stat(ncr)))

print()
print('=== robustness: distribution per bucket (check fat-tail / >1000% glitches) ===')
for nm in order:
    v=sorted(b[nm])
    if not v: continue
    n=len(v)
    over1000=sum(1 for x in v if x>1000)
    p10=v[int(0.10*n)]; p90=v[int(0.90*n)]
    # winsorized mean at +/-100%
    w=[max(-100,min(100,x)) for x in v]
    import statistics as st
    print("{:18} n={:>4} min={:>8.1f} p10={:>7.1f} p90={:>7.1f} max={:>9.1f} >1000%={} winsor100_mean={:>6.2f}".format(
        nm,n,v[0],p10,p90,v[-1],over1000,st.mean(w)))

print()
print('=== time-split robustness of deep cohort (mtf<=-4) by signal_ts_ms median split ===')
import statistics as st
deep4=[p for p in positions if p['mtf']<=-4 and p['em'].get('signal_ts_ms')]
deep4.sort(key=lambda p:p['em']['signal_ts_ms'])
half=len(deep4)//2
for label,seg in [('early half',deep4[:half]),('late half',deep4[half:])]:
    v=[p['pnl'] for p in seg]
    print("  {:10} n={:>3} mean={:>6.2f} med={:>6.2f} WR={:>5.1f}".format(label,len(v),st.mean(v),st.median(v),100*sum(1 for x in v if x>0)/len(v)))

print()
print('=== CORRECTED GATE candidates (winsorized@100 to resist tail) ===')
def wstat(v):
    w=[max(-100,min(100,x)) for x in v]
    n=len(v); return "n={:>4} mean={:>6.2f} wmean={:>6.2f} med={:>6.2f} WR={:>5.1f}".format(
        n, st.mean(v), st.mean(w), st.median(v), 100*sum(1 for x in v if x>0)/n)
mid=[p['pnl'] for p in positions if -3<=p['mtf']<=-1]
midstrict=[p['pnl'] for p in positions if -2>=p['mtf']>=-3]  # current-ish blocked excl -4
deeponly=[p['pnl'] for p in positions if p['mtf']<=-4]
passz=[p['pnl'] for p in positions if p['mtf']>=0]
print('  block band [-3..-1] (middle trough):', wstat(mid))
print('  block band [-3..-2] (excl -1):       ', wstat(midstrict))
print('  PASS deep <=-4:                       ', wstat(deeponly))
print('  PASS zone >=0:                        ', wstat(passz))
print('  current gate blocks <=-2 (=-2,-3,-4): ', wstat([p['pnl'] for p in positions if p['mtf']<=-2]))
