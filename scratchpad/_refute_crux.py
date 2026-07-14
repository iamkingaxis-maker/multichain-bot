import json, statistics as st
from collections import defaultdict
from datetime import datetime

rows=json.load(open('scratchpad/_joined_rows.json'))
rows=[r for r in rows if r['age'] is not None]

# ---- Repeat-buy over-weighting: do WINNING fresh tokens get more legs? ----
def tok_legs(lo,hi):
    d=defaultdict(list)
    for r in rows:
        if lo<=r['age']<hi: d[r['addr']].append(r)
    return d
print('=== <2h fresh tokens: legs per token, split by token-median realized ===')
d=tok_legs(0,2)
win=[]; los=[]
for addr,ts in d.items():
    reals=[x['real'] for x in ts if x['real'] is not None]
    if not reals: continue
    m=st.median(reals)
    (win if m>0 else los).append(len(ts))
print('winning tokens (median>0): n=%d, total legs=%d, mean legs/token=%.2f'%(len(win),sum(win),sum(win)/len(win) if win else 0))
print('losing  tokens (median<=0): n=%d, total legs=%d, mean legs/token=%.2f'%(len(los),sum(los),sum(los)/len(los) if los else 0))

# ---- Per-token metric, <2h vs old, with realizable TP/stop sim ----
def et2(vals):
    v=sorted([x for x in vals if x is not None])
    if len(v)<=2: return None
    return st.median(v[:-2])
def med(vals):
    v=[x for x in vals if x is not None]
    return st.median(v) if v else None

# Realizable strategy per LEG: TP at +T if peak>=T; elif mae<=-S stop at -S; else actual realized (bot exit)
def sim_leg(r,T,S):
    if r['peak'] is not None and r['peak']>=T: return T
    if r['mae'] is not None and r['mae']<=-S: return -S
    return r['real']  # fall back to actual exit
def tok_agg(lo,hi):
    d=defaultdict(list)
    for r in rows:
        if lo<=r['age']<hi: d[r['addr']].append(r)
    out=[]
    for addr,ts in d.items():
        reals=[x['real'] for x in ts if x['real'] is not None]
        peaks=[x['peak'] for x in ts if x['peak'] is not None]
        maes=[x['mae'] for x in ts if x['mae'] is not None]
        out.append(dict(addr=addr,n=len(ts),
            real=med(reals),peak=max(peaks) if peaks else None,
            id&60=(0.6*max(peaks)) if peaks else None,
            legs=ts))
    return out

bands=[('<2h',0,2),('2-6h',2,6),('6-24h',6,24),('24-168h',24,168),('>=168h',168,1e9)]
print('\n=== PER-TOKEN realized vs realizable exits (ex-top-2 token-median) ===')
print('%-8s %5s %9s %9s %10s | %s'%('band','nTok','exT2 real','exT2 0.6MFE','exT2 medPeak','realizable TP/stop grids (exT2)'))
grids=[(10,15),(15,20),(20,25),(25,30),(30,40)]
for lab,lo,hi in bands:
    d=defaultdict(list)
    for r in rows:
        if lo<=r['age']<hi: d[r['addr']].append(r)
    toks=[]
    for addr,ts in d.items():
        reals=[x['real'] for x in ts if x['real'] is not None]
        peaks=[x['peak'] for x in ts if x['peak'] is not None]
        toks.append(dict(real=med(reals),peak=max(peaks) if peaks else None,legs=ts))
    e_real=et2([t['real'] for t in toks])
    e_60=et2([0.6*t['peak'] for t in toks if t['peak'] is not None])
    e_pk=et2([t['peak'] for t in toks])
    gstr=[]
    for T,S in grids:
        simtok=[med([sim_leg(l,T,S) for l in t['legs']]) for t in toks]
        gstr.append('%d/-%d:%s'%(T,S,('%.1f'%et2(simtok)) if et2(simtok) is not None else '-'))
    def f(x): return '%9.2f'%x if x is not None else '    -    '
    print('%-8s %5d %s %s %s | %s'%(lab,len(toks),f(e_real),f(e_60),f(e_pk),'  '.join(gstr)))
