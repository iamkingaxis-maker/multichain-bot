import json, statistics as st
from collections import defaultdict, Counter
T=[t for t in json.load(open('scratchpad/sol_selection/_trips.json')) if t.get('ret') is not None]
legc=Counter(t['token'] for t in T); TOP2=set(k for k,_ in legc.most_common(2))
N_ALL=len(T); DEEP=lambda t:(t.get('pc_h1') or 0)<=-45.0; N_DEEP=sum(1 for t in T if DEEP(t))
def tokmed(tr):
    by=defaultdict(list)
    for t in tr:
        if t['token'] in TOP2: continue
        by[t['address']].append(t['ret'])
    per=[st.median(v) for v in by.values()]; return (st.median(per),len(per)) if per else (None,0)
def wr(tr): return 100*sum(1 for t in tr if t['ret']>0)/len(tr) if tr else None
def p90(tr): r=sorted(t['ret'] for t in tr); return r[int(len(r)*0.9)] if r else None
def fmt(x): return f"{x:+.1f}" if isinstance(x,(int,float)) else "  -"
def splits():
    s=sorted(T,key=lambda t:t['sell_time'] or t['time'] or ''); mid=len(s)//2
    return {'CH1':s[:mid],'CH2':s[mid:],
            'ODD':[t for t in T if int((t['time'] or '2026-01-01')[8:10])%2==1],
            'EVEN':[t for t in T if int((t['time'] or '2026-01-01')[8:10])%2==0]}
SPL=splits()

print("== LIQ FLOOR NEIGHBORHOOD within DEEP cohort (overfit check) ==")
print(f"  {'liq>=':>7}{'n':>5}{'vol%deep':>9}{'ALL_tm':>8}{'CH1':>7}{'CH2':>7}{'ODD':>7}{'EVEN':>7}{'green/4':>8}")
for thr in [20000,25000,28000,30000,32000,35000,40000]:
    cond=lambda t:DEEP(t) and (t.get('liq') or 0)>=thr
    n=sum(1 for t in T if cond(t))
    am,_=tokmed([t for t in T if cond(t)])
    hs=[]; g=0
    for nm in ['CH1','CH2','ODD','EVEN']:
        pm,_=tokmed([t for t in SPL[nm] if cond(t)]); hs.append(pm)
        if pm is not None and pm>=0: g+=1
    print(f"  {thr:>7}{n:>5}{n/N_DEEP*100:>8.0f}%{fmt(am):>8}"+"".join(fmt(h).rjust(7) for h in hs)+f"{g:>6}/4")

print("\n== INTERACTION: liq FLOOR alone (no deep) — is deep needed? ==")
print(f"  {'group':<28}{'n':>5}{'ALL_tm':>8}{'wr':>6}{'p90':>7}")
for lbl,cond in [
    ('all trips',lambda t:True),
    ('liq>=30k (no deep gate)',lambda t:(t.get('liq') or 0)>=30000),
    ('DEEP only',lambda t:DEEP(t)),
    ('DEEP + liq>=30k',lambda t:DEEP(t) and (t.get('liq') or 0)>=30000),
    ('SHALLOW(pc_h1>-45) + liq>=30k',lambda t:not DEEP(t) and (t.get('liq') or 0)>=30000),
]:
    tr=[t for t in T if cond(t)]; am,an=tokmed(tr)
    print(f"  {lbl:<28}{len(tr):>5}{fmt(am):>8}{fmt(wr(tr)):>6}{fmt(p90(tr)):>7}  ({an} tok)")

print("\n== deep-flush DEPTH sweep at fixed liq>=30k (is -45 special or is any deep+liq green?) ==")
print(f"  {'pc_h1<=':>8}{'n':>5}{'ALL_tm':>8}{'green/4':>8}")
for d in [-30,-35,-40,-45,-50,-55]:
    cond=lambda t:(t.get('pc_h1') or 0)<=d and (t.get('liq') or 0)>=30000
    n=sum(1 for t in T if cond(t)); am,_=tokmed([t for t in T if cond(t)])
    g=sum(1 for nm in ['CH1','CH2','ODD','EVEN'] if (tokmed([t for t in SPL[nm] if cond(t)])[0] or -9)>=0)
    print(f"  {d:>8}{n:>5}{fmt(am):>8}{g:>6}/4")
