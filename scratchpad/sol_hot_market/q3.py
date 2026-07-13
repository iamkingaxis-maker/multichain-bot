import json, statistics as st
from collections import defaultdict
from datetime import datetime
d = json.load(open('scratchpad/sol_selection/_trips.json'))
for t in d: t['dt']=datetime.fromisoformat(t['time'])
d.sort(key=lambda t:t['dt'])
def pctl(xs,p):
    if not xs: return float('nan')
    xs=sorted(xs); k=(len(xs)-1)*p/100.0; f=int(k); c=min(f+1,len(xs)-1)
    return xs[f]+(xs[c]-xs[f])*(k-f)
def med(xs): return st.median(xs) if xs else float('nan')
def mean(xs): return st.mean(xs) if xs else float('nan')

# TRAILING universe-heat signal, computed ONLY from trips strictly before current (no leakage).
K=25
for i,t in enumerate(d):
    prev=d[max(0,i-K):i]
    if len(prev)>=10:
        # trailing reach>=20 rate = market heat proxy (decision-time knowable)
        t['heat'] = sum(1 for p in prev if p['peak']>=20)/len(prev)
        t['heat_avgpeak'] = mean([p['peak'] for p in prev])
    else:
        t['heat']=None; t['heat_avgpeak']=None

usable=[t for t in d if t['heat'] is not None]
print(f"usable trips with trailing heat signal: {len(usable)}")

# Global relationship: split by trailing heat tercile, forward peak outcomes
def report(coh, label):
    hi=[t for t in coh if t['heat']>= pctl([x['heat'] for x in coh],66)]
    lo=[t for t in coh if t['heat']<= pctl([x['heat'] for x in coh],33)]
    def stats(g):
        pk=[t['peak'] for t in g]
        r20=sum(1 for t in g if t['peak']>=20)/len(g) if g else float('nan')
        r30=sum(1 for t in g if t['peak']>=30)/len(g) if g else float('nan')
        return pctl(pk,75), r20*100, r30*100, mean([t['ret'] for t in g])
    hp,h20,h30,hret=stats(hi); lp,l20,l30,lret=stats(lo)
    print(f"{label:10s} HIGH-heat n={len(hi):3d}: peakP75={hp:5.1f} reach20={h20:4.1f}% reach30={h30:4.1f}% meanRet={hret:6.2f}")
    print(f"{'':10s} LOW-heat  n={len(lo):3d}: peakP75={lp:5.1f} reach20={l20:4.1f}% reach30={l30:4.1f}% meanRet={lret:6.2f}")
    return h20-l20  # reach20 spread

print("\n=== Q3: trailing-heat HIGH vs LOW, forward outcomes (whole sample) ===")
report(usable,'ALL')

print("\n=== Q3: 4-half (quartile) OOS — does HIGH-heat beat LOW-heat in EACH quarter? ===")
n=len(usable); q=n//4
spreads=[]
for i in range(4):
    seg=usable[i*q: (i+1)*q if i<3 else n]
    s=report(seg,f'Q{i+1}')
    spreads.append(s)
    print()
print("reach20 spread (HIGH-LOW) by quarter:", [f'{s:+.1f}' for s in spreads])
print("holds in all 4:", all(s>0 for s in spreads))

# Also: SOL trend not in data; check entry chart_mtf_align as heat conditioner
print("\n=== Q3b: chart_mtf_align (per-trip, decision-time) vs forward peak ===")
byal=defaultdict(list)
for t in d: byal[t.get('chart_mtf_align')].append(t)
for al,g in sorted(byal.items(), key=lambda kv:-len(kv[1])):
    pk=[t['peak'] for t in g]
    r30=sum(1 for t in g if t['peak']>=30)/len(g)*100
    print(f"{str(al):14s} n={len(g):3d} peakP75={pctl(pk,75):5.1f} reach30={r30:4.1f}% meanRet={mean([t['ret'] for t in g]):6.2f}")
