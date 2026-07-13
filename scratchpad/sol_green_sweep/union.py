"""Union / orthogonality analysis: does adding a green cell to deep+liq expand
volume while STAYING green? Report union stats + the INCREMENTAL slice (cand minus deep+liq)."""
import json, statistics as st
from collections import defaultdict, Counter

T = [t for t in json.load(open('scratchpad/sol_selection/_trips.json')) if t.get('ret') is not None]
legc = Counter(t['token'] for t in T); TOP2 = set(k for k,_ in legc.most_common(2))
N = len(T)
def g(t,k): return t.get(k)

def tokmed(trips):
    by=defaultdict(list)
    for t in trips:
        if t['token'] in TOP2: continue
        by[t['address']].append(t['ret'])
    per=[st.median(v) for v in by.values()]
    if not per: return (None,0,None)
    return (st.median(per), len(per), 100*sum(1 for x in per if x>0)/len(per))
def p90(trips):
    r=sorted(t['ret'] for t in trips); return r[int(len(r)*0.9)] if r else None

def splits():
    s=sorted(T,key=lambda t:t['sell_time'] or t['time'] or ''); mid=len(s)//2
    def day(t):
        try: return int((t['time'] or '2026-01-01')[8:10])
        except: return 1
    return {'CH1':set(id(t) for t in s[:mid]),'CH2':set(id(t) for t in s[mid:]),
            'ODD':set(id(t) for t in T if day(t)%2==1),'EVEN':set(id(t) for t in T if day(t)%2==0)}
SPL=splits()
def fmt(x): return f"{x:+.1f}" if isinstance(x,(int,float)) else "  -"

def report(name, pred):
    sub=[t for t in T if pred(t)]
    m,nt,gf=tokmed(sub)
    halves=[]
    for h in ['CH1','CH2','ODD','EVEN']:
        hv=[t for t in sub if id(t) in SPL[h]]; hm,hn,_=tokmed(hv); halves.append((h,hm,hn))
    ng=sum(1 for _,hm,_ in halves if hm is not None and hm>0)
    print(f"{name:<40} vol={len(sub)/N*100:>5.1f}%  ex2med={fmt(m):>6}  grn={gf if gf else 0:>3.0f}%  ntok={nt:>3}  p90={fmt(p90(sub)):>6}  {ng}/4  " + " ".join(f"{h}{fmt(hm)}({hn})" for h,hm,hn in halves))
    return dict(m=m,nt=nt,gf=gf,ng=ng,vol=len(sub)/N)

DEEP=lambda t: g(t,'pc_h1') is not None and g(t,'pc_h1')<=-45
LIQ30=lambda t: g(t,'liq') is not None and g(t,'liq')>=30000
BASE=lambda t: DEEP(t) and LIQ30(t)

CANDS={
 'liq>=35k & ubuy>=50':      lambda t: (g(t,'liq') or 0)>=35000 and (g(t,'unique_buyers_n') or 0)>=50,
 'liq>=45k & bsh1>=1.6':     lambda t: (g(t,'liq') or 0)>=45000 and (g(t,'bs_h1') or 0)>=1.6,
 'mtf<=-1 & bp60<0.52':      lambda t: (g(t,'chart_mtf_score') is not None and g(t,'chart_mtf_score')<=-1) and (g(t,'buy_pressure_60s') is not None and g(t,'buy_pressure_60s')<0.52),
 'ubuy>=50 & mtf<=-1':       lambda t: (g(t,'unique_buyers_n') or 0)>=50 and (g(t,'chart_mtf_score') is not None and g(t,'chart_mtf_score')<=-1),
 'liq>=45k & bsh1>=1.35':    lambda t: (g(t,'liq') or 0)>=45000 and (g(t,'bs_h1') or 0)>=1.35,
 'vdeep(h1<=-55) & liq>=30k':lambda t: (g(t,'pc_h1') or 0)<=-55 and (g(t,'liq') or 0)>=30000,
}

print("=== BASELINE deep+liq>=30k ===")
report('deep+liq>=30k (BASE)', BASE)
print("\n=== CANDIDATE cells (standalone) ===")
for nm,p in CANDS.items(): report(nm, p)

print("\n=== INCREMENTAL slice: candidate AND NOT base (the new volume it adds) ===")
for nm,p in CANDS.items():
    inc=(lambda p: (lambda t: p(t) and not BASE(t)))(p)
    report(f"[+] {nm} \\ base", inc)

print("\n=== UNION: base OR candidate (total coverage) ===")
for nm,p in CANDS.items():
    uni=(lambda p: (lambda t: BASE(t) or p(t)))(p)
    report(f"BASE U {nm}", uni)
