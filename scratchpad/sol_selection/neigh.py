import json, statistics as st
from collections import defaultdict, Counter
T=[t for t in json.load(open('scratchpad/sol_selection/_trips.json')) if t.get('ret') is not None]
legc=Counter(t['token'] for t in T); TOP2=set(k for k,_ in legc.most_common(2))
def tokmed(trips):
    by=defaultdict(list)
    for t in trips:
        if t['token'] in TOP2: continue
        by[t['address']].append(t['ret'])
    per=[st.median(v) for v in by.values()]; return (st.median(per),len(per)) if per else (None,0)
def med(xs): return st.median(xs) if xs else None
def fmt(x): return f"{x:+.1f}" if isinstance(x,(int,float)) else " -"

print("== NEIGHBORHOOD: pc_h1 threshold sweep (pass=below thr) ==")
print(f"  {'thr':>5}{'PASS ex2':>10}{'nTok':>6}{'FAIL ex2':>10}{'nTok':>6}{'gap':>6}")
for thr in [-35,-40,-45,-50,-55,-60]:
    pv=[t for t in T if (t.get('pc_h1') or 0)<=thr]; fv=[t for t in T if (t.get('pc_h1') or 0)>thr]
    pm,pn=tokmed(pv); fm,fn=tokmed(fv)
    print(f"  {thr:>5}{fmt(pm):>10}{pn:>6}{fmt(fm):>10}{fn:>6}{fmt(pm-fm) if pm and fm else ' -':>6}")

print("\n== ENTRY vs EXIT artifact check (deep pc_h1<=-45 vs rest) ==")
deep=[t for t in T if (t.get('pc_h1') or 0)<=-45]; rest=[t for t in T if (t.get('pc_h1') or 0)>-45]
for lbl,g in [('DEEP<=-45',deep),('REST',rest)]:
    peaks=[t['peak'] for t in g if t.get('peak') is not None]
    maes=[t['mae'] for t in g if t.get('mae') is not None]
    holds=[t['hold'] for t in g if t.get('hold') is not None]
    rets=[t['ret'] for t in g]
    print(f"  {lbl:<10} med_peak={fmt(med(peaks))}  med_mae={fmt(med(maes))}  med_hold={med(holds):.0f}s  med_ret={fmt(med(rets))}  n={len(g)}")
print("  (higher med_peak in DEEP => entry catches more upside = selection, not exit)")

print("\n== proven-volume RH axis, isolated (does high pre-vol help? RH says yes) ==")
for lbl,lo,hi in [('vol<400k',0,400000),('400-1M',400000,1000000),('1-2M',1000000,2000000),('>2M',2000000,9e18)]:
    g=[t for t in T if lo<=(t.get('entry_vol_h24') or 0)<hi]
    pm,pn=tokmed(g); print(f"  {lbl:<10} tokmed={fmt(pm)} nTok={pn}")
