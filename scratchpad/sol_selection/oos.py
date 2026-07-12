import json, statistics as st
from collections import defaultdict, Counter

T = [t for t in json.load(open('scratchpad/sol_selection/_trips.json')) if t.get('ret') is not None]
legc = Counter(t['token'] for t in T)
TOP2 = set(tok for tok,_ in legc.most_common(2))

def tokmed(trips, ex2=True):
    by = defaultdict(list)
    for t in trips:
        if ex2 and t['token'] in TOP2: continue
        by[t['address']].append(t['ret'])
    per=[st.median(v) for v in by.values()]
    return (st.median(per), len(per)) if per else (None,0)

def wr(trips):
    r=[t for t in trips]
    return 100*sum(1 for t in r if t['ret']>0)/len(r) if r else None

def splits(trips):
    s=sorted(trips,key=lambda t:t['sell_time'] or t['time'] or '')
    mid=len(s)//2
    ch1,ch2=s[:mid],s[mid:]
    odd=[t for t in trips if int((t['time'] or '2026-01-01')[8:10])%2==1]
    even=[t for t in trips if int((t['time'] or '2026-01-01')[8:10])%2==0]
    return {'CHRONO1':ch1,'CHRONO2':ch2,'ODD':odd,'EVEN':even}

def fmt(x): return f"{x:+.1f}" if isinstance(x,(int,float)) else " -"

def test(axis, op, thr, label):
    """op: 'le' pass if val<=thr, 'ge' pass if val>=thr"""
    def passes(t):
        v=t.get(axis)
        if v is None: return None
        return (v<=thr) if op=='le' else (v>=thr)
    print(f"\n### {label}  [{axis} {op} {thr}]")
    print(f"  {'split':<9}{'PASS ex2':>9}{'nTok':>6}{'wr':>6}  |{'FAIL ex2':>9}{'nTok':>6}{'wr':>6}  gap")
    ok=True
    for name, sub in {'ALL':T, **splits(T)}.items():
        pv=[t for t in sub if passes(t)==True]
        fv=[t for t in sub if passes(t)==False]
        pm,pn=tokmed(pv); fm,fn=tokmed(fv)
        gap = (pm-fm) if (pm is not None and fm is not None) else None
        if name!='ALL' and (gap is None or gap<=0): ok=False
        print(f"  {name:<9}{fmt(pm):>9}{pn:>6}{fmt(wr(pv)):>6}  |{fmt(fm):>9}{fn:>6}{fmt(wr(fv)):>6}  {fmt(gap)}")
    print(f"  --> holds in all 4 halves (gap>0): {ok}")

# promising single-axis separators (pass = the LESS-red side)
test('chart_mtf_score','le',-1,'MTF downtrend (<=-1)')
test('chart_score','le',47,'low chart-score (<=47)')
test('pc_h1','le',-45,'deep 1h dip (<=-45)')
test('h24_ratio_to_peak','le',0.35,'deep below h24 peak (ratio<=0.35)')
test('pct_off_peak','le',-800,'very deep off-peak (<=-800)')
test('lifecycle_peak_h24_pct','ge',750,'high 24h peak-pump (>=750)')
