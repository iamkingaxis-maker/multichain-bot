import json, statistics as st
from collections import defaultdict
from datetime import datetime
d = json.load(open('scratchpad/sol_selection/_trips.json'))
for t in d:
    t['dt'] = datetime.fromisoformat(t['time']); t['day']=t['dt'].strftime('%Y-%m-%d')
def pctl(xs,p):
    if not xs: return float('nan')
    xs=sorted(xs); k=(len(xs)-1)*p/100.0; f=int(k); c=min(f+1,len(xs)-1)
    return xs[f]+(xs[c]-xs[f])*(k-f)
def med(xs): return st.median(xs) if xs else float('nan')

recent=[t for t in d if t['day']>='2026-07-10']
prior =[t for t in d if '2026-07-06'<=t['day']<='2026-07-09']

TP1,TP2=6.0,12.0
print("=== Q2: TP gap — of trips that would HIT TP2 (peak>=12), how far did they actually run? ===")
for lbl,coh in [('recent',recent),('prior',prior),('ALL',d)]:
    hit2=[t['peak'] for t in coh if t['peak']>=TP2]
    n=len(coh)
    print(f"\n{lbl}: n={n}  trips reaching +12 = {len(hit2)} ({len(hit2)/n*100:.1f}%)")
    if hit2:
        print(f"   peak of TP2-reachers: p50={pctl(hit2,50):.1f} p75={pctl(hit2,75):.1f} p90={pctl(hit2,90):.1f} max={max(hit2):.1f}")
        left=[p-TP2 for p in hit2]
        print(f"   bounce LEFT after +12 exit: p50={pctl(left,50):.1f} p75={pctl(left,75):.1f} mean={st.mean(left):.1f}")
    hit1=[t['peak'] for t in coh if t['peak']>=TP1]
    print(f"   trips reaching +6 = {len(hit1)} ({len(hit1)/n*100:.1f}%)")

print("\n=== Q2b: conditional continuation — given reached +12, P(reach +20/+30/+50) ===")
for lbl,coh in [('recent',recent),('prior',prior)]:
    base=[t for t in coh if t['peak']>=TP2]
    if not base: continue
    print(f"{lbl}: given +12 (n={len(base)}):", end=" ")
    for thr in [20,30,50]:
        p=sum(1 for t in base if t['peak']>=thr)/len(base)
        print(f"P(+{thr})={p*100:.0f}%", end="  ")
    print()

print("\n=== Q2c: green trips only (peak>0) peak dist ===")
for lbl,coh in [('recent',recent),('prior',prior)]:
    g=[t['peak'] for t in coh if t['peak']>0]
    print(f"{lbl}: green n={len(g)} p50={pctl(g,50):.1f} p75={pctl(g,75):.1f} p90={pctl(g,90):.1f}")

print("\n=== Q2d: time-to-peak (minutes) for TP2-reachers — do they run fast or need holding? ===")
# peak minutes not directly stored; use hold as proxy for realized exit. Check 'hold' vs peak for winners.
for lbl,coh in [('recent',recent),('prior',prior)]:
    win=[t for t in coh if t['peak']>=TP2]
    holds=[t['hold'] for t in win]
    rets=[t['ret'] for t in win]
    print(f"{lbl}: TP2-reachers realized hold p50={pctl(holds,50):.0f}s p90={pctl(holds,90):.0f}s ; realized ret p50={pctl(rets,50):.1f} (vs peak p50 {pctl([t['peak'] for t in win],50):.1f})")
