import json, statistics as st
from collections import defaultdict
from datetime import datetime

rows=json.load(open('scratchpad/_joined_rows.json'))
for r in rows:
    r['ent']=datetime.fromisoformat(r['ent']) if r['ent'] else None

# per-token aggregate by address (claim method)
bytok=defaultdict(list)
for r in rows:
    if r['age'] is None: continue
    bytok[r['addr']].append(r)
toks=[]
for addr,ts in bytok.items():
    ts=sorted(ts,key=lambda x:(x['ent'] or datetime.min))
    first=ts[0]
    peaks=[x['peak'] for x in ts if x['peak'] is not None]
    reals=[x['real'] for x in ts if x['real'] is not None]
    maes=[x['mae'] for x in ts if x['mae'] is not None]
    toks.append(dict(addr=addr, age=first['age'], stage=first['stage'], nf5=first['nf5'],
        pc_h1=first['pc_h1'], mcap=first['mcap'], ent=first['ent'],
        peak=max(peaks) if peaks else None,
        real=st.median(reals) if reals else None,
        mae=min(maes) if maes else None,
        ntrips=len(ts)))
print('unique tokens with age:',len(toks))

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

age_bins=[('<1h',0,1),('1-2h',1,2),('2-6h',2,6),('6-24h',6,24),('24-72h',24,72),
    ('72-168h',72,168),('168-720h',168,720),('>=720h',720,1e9)]

print('\n=== FRESHNESS GRADIENT (per-token, by lifecycle_age) ===')
print('%-10s %5s %8s %8s %8s %8s %8s %9s %9s %9s'%('band','nTok','r>=10%','r>=20%','r>=50%','medPeak','exT2Real','medReal','rug<=-50','loss<0'))
for lab,lo,hi in age_bins:
    g=[t for t in toks if lo<=t['age']<hi]
    if not g: print('%-10s (empty)'%lab); continue
    peaks=[t['peak'] for t in g]; reals=[t['real'] for t in g]
    rug=100.0*sum(1 for t in g if (t['real'] is not None and t['real']<=-50) or (t['mae'] is not None and t['mae']<=-50))/len(g)
    lossn=100.0*sum(1 for t in g if t['real'] is not None and t['real']<0)/len([t for t in g if t['real'] is not None])
    def f(x): return '%8.2f'%x if x is not None else '     -  '
    def p(x): return '%8.1f'%x if x is not None else '     -  '
    print('%-10s %5d %s %s %s %s %s %s %8.1f %8.1f'%(lab,len(g),
        p(rate(peaks,10)),p(rate(peaks,20)),p(rate(peaks,50)),
        f(med(peaks)),f(et2(reals)),f(med(reals)),rug,lossn))

# allocation
tot=len(toks)
print('\nALLOCATION by age:')
for lab,lo,hi in age_bins:
    n=sum(1 for t in toks if lo<=t['age']<hi)
    print('  %-10s %5d  %.1f%%'%(lab,n,100.0*n/tot))
