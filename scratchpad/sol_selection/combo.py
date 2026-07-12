import json, statistics as st
from collections import defaultdict, Counter

T = [t for t in json.load(open('scratchpad/sol_selection/_trips.json')) if t.get('ret') is not None]
legc = Counter(t['token'] for t in T)
TOP2 = set(tok for tok,_ in legc.most_common(2))

def tokmed(trips):
    by=defaultdict(list)
    for t in trips:
        if t['token'] in TOP2: continue
        by[t['address']].append(t['ret'])
    per=[st.median(v) for v in by.values()]
    return (st.median(per), len(per)) if per else (None,0)
def wr(trips): return 100*sum(1 for t in trips if t['ret']>0)/len(trips) if trips else None
def p90(trips):  # fat-tail: 90th pct return (winner-kill check)
    r=sorted(t['ret'] for t in trips); return r[int(len(r)*0.9)] if r else None
def splits(trips):
    s=sorted(trips,key=lambda t:t['sell_time'] or t['time'] or ''); mid=len(s)//2
    return {'CH1':s[:mid],'CH2':s[mid:],
            'ODD':[t for t in trips if int((t['time'] or '2026-01-01')[8:10])%2],
            'EVEN':[t for t in trips if not int((t['time'] or '2026-01-01')[8:10])%2]}
def fmt(x): return f"{x:+.1f}" if isinstance(x,(int,float)) else " -"

def test(pred, label):
    print(f"\n### {label}")
    print(f"  {'split':<6}{'PASS':>7}{'nTok':>6}{'wr':>6}{'p90':>7}  |{'FAIL':>7}{'nTok':>6}{'wr':>6}")
    ok=True; enough=True
    for name, sub in {'ALL':T, **splits(T)}.items():
        pv=[t for t in sub if pred(t)==True]; fv=[t for t in sub if pred(t)==False]
        pm,pn=tokmed(pv); fm,fn=tokmed(fv)
        gap=(pm-fm) if (pm is not None and fm is not None) else None
        if name!='ALL':
            if gap is None or gap<=0: ok=False
            if pn<20 or fn<20: enough=False
        print(f"  {name:<6}{fmt(pm):>7}{pn:>6}{fmt(wr(pv)):>6}{fmt(p90(pv)):>7}  |{fmt(fm):>7}{fn:>6}{fmt(wr(fv)):>6}")
    print(f"  --> gap>0 all 4 halves: {ok} | n>=20/side/half: {enough}")

def g(t,k):
    v=t.get(k); return v

# RH signature port: MODERATE dip + EARLY arc + PROVEN volume (should FAIL if lane inverts)
test(lambda t: (g(t,'pc_h1') is not None and -20>=g(t,'pc_h1')>=-40) and (g(t,'lifecycle_peak_h24_pct') or 0)<750 and (g(t,'entry_vol_h24') or 0)>=800000,
     'RH-PORT: moderate dip(-20..-40) + early arc(peak<750) + proven vol(>=800k)')
# RH inverse (what single axes suggest): DEEP dip + PROVEN volume
test(lambda t: (g(t,'pc_h1') or 0)<=-45 and (g(t,'entry_vol_h24') or 0)>=800000,
     'DEEP dip(<=-45) + proven vol(>=800k)')
# Deep dip alone (best single)
test(lambda t: (g(t,'pc_h1') or 0)<=-45, 'DEEP dip pc_h1<=-45 (best single)')
# Deep dip + downtrend
test(lambda t: (g(t,'pc_h1') or 0)<=-45 and (g(t,'chart_mtf_score') if g(t,'chart_mtf_score') is not None else 9)<=0,
     'DEEP dip<=-45 AND mtf<=0 (downtrend)')
# Deep off-peak + deep h1
test(lambda t: (g(t,'pct_off_peak') or 0)<=-800 and (g(t,'pc_h1') or 0)<=-30,
     'very deep off-peak<=-800 AND pc_h1<=-30')
# BASELINE structure edge to beat: pc_h6>=0 OR liq>=48k
test(lambda t: (g(t,'pc_h6') or -999)>=0 or (g(t,'liq') or 0)>=48000,
     'BASELINE: pc_h6>=0 OR liq>=48k')
# BASELINE buyer>=$34
test(lambda t: (g(t,'mean_buy_usd') or 0)>=34, 'BASELINE: mean_buy>=$34')
# BASELINE down-MTF dip
test(lambda t: (g(t,'chart_mtf_score') if g(t,'chart_mtf_score') is not None else 9)<0, 'BASELINE: mtf<0 (down-MTF)')
