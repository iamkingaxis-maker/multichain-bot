import json, statistics as st
from collections import defaultdict, Counter
from datetime import datetime

rows=json.load(open('scratchpad/_joined_rows.json'))
rows=[r for r in rows if r['age'] is not None]
print('positions w/ age:',len(rows),' unique addrs:',len(set(r['addr'] for r in rows)))

def et2(vals):
    v=sorted([x for x in vals if x is not None])
    if len(v)<=2: return None
    return st.median(v[:-2])
def rate(vals,thr):
    v=[x for x in vals if x is not None]
    return 100.0*sum(1 for x in v if x>=thr)/len(v) if v else None
def med(vals):
    v=[x for x in vals if x is not None]
    return st.median(v) if v else None

age_bins=[('<1h',0,1),('1-2h',1,2),('<2h',0,2),('2-6h',2,6),('6-24h',6,24),('24-72h',24,72),
    ('72-168h',72,168),('168-720h',168,720),('>=168h',168,1e9)]

print('\n=== PER-LEG (per-position, NOT deduped) freshness gradient ===')
print('%-10s %6s %8s %8s %8s %9s %9s %8s'%('band','nPos','r>=10%','r>=20%','exT2Real','medReal','meanReal','rug<=-50'))
for lab,lo,hi in age_bins:
    g=[r for r in rows if lo<=r['age']<hi]
    if not g: print('%-10s (empty)'%lab); continue
    peaks=[r['peak'] for r in g]; reals=[r['real'] for r in g]
    rug=100.0*sum(1 for r in g if (r['real'] is not None and r['real']<=-50) or (r['mae'] is not None and r['mae']<=-50))/len(g)
    def f(x): return '%8.2f'%x if x is not None else '   -    '
    def p(x): return '%8.1f'%x if x is not None else '   -    '
    print('%-10s %6d %s %s %s %s %s %8.1f'%(lab,len(g),
        p(rate(peaks,10)),p(rate(peaks,20)),f(et2(reals)),f(med(reals)),
        f(st.mean([x for x in reals if x is not None])),rug))

# How many UNIQUE addresses in each age band (per-leg inflation factor)
print('\n=== unique addrs vs positions per band ===')
for lab,lo,hi in age_bins:
    g=[r for r in rows if lo<=r['age']<hi]
    ua=len(set(r['addr'] for r in g))
    print('  %-10s pos=%5d uniq_addr=%4d  inflation=%.1fx'%(lab,len(g),ua,len(g)/ua if ua else 0))
