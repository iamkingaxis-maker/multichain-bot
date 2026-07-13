import json, statistics as st
from datetime import datetime
d=json.load(open('scratchpad/sol_selection/_trips.json'))
for t in d: t['dt']=datetime.fromisoformat(t['time'])
d.sort(key=lambda t:t['dt'])
def mean(xs): return st.mean(xs) if xs else float('nan')
def pctl(xs,p):
    if not xs: return float('nan')
    xs=sorted(xs);k=(len(xs)-1)*p/100.0;f=int(k);c=min(f+1,len(xs)-1)
    return xs[f]+(xs[c]-xs[f])*(k-f)

K=25
for i,t in enumerate(d):
    prev=d[max(0,i-K):i]
    t['heat']= (sum(1 for p in prev if p['peak']>=20)/len(prev)) if len(prev)>=10 else None

HEAT_THR=0.20   # trailing reach20 >= 20% => HIGH regime
TP1,TP2=6.0,12.0
GIVEBACK=8.0    # trailing runner: exit at peak - 8pp once past TP2

def sim_current(t):
    # approx current: TP2 cap at +12 if reached, else realized ret from data
    # use actual realized ret (already reflects current exits)
    return t['ret']

def sim_runner(t, regime_gate):
    # if HIGH regime AND token reaches TP2, hold with trailing stop giveback; else current
    hot = (t['heat'] is not None and t['heat']>=HEAT_THR)
    if regime_gate and not hot:
        return t['ret']
    if t['peak']>=TP2:
        # trailing: capture peak minus giveback, floored at TP2 (we'd have TP2 in hand)
        return max(TP2, t['peak']-GIVEBACK)
    return t['ret']

usable=[t for t in d if t['heat'] is not None]
def portfolio(fn,coh):
    rets=[fn(t) for t in coh]
    return mean(rets), pctl(rets,50)

print("=== Q4: portfolio mean-ret per trip (approx, %; friction not modeled) ===")
base_m,base_med=portfolio(sim_current,usable)
reg_m,reg_med=portfolio(lambda t:sim_runner(t,True),usable)
bl_m,bl_med=portfolio(lambda t:sim_runner(t,False),usable)
print(f"CURRENT (realized)        mean={base_m:6.2f} med={base_med:6.2f}")
print(f"RUNNER regime-gated(HIGH) mean={reg_m:6.2f} med={reg_med:6.2f}  delta={reg_m-base_m:+.2f}")
print(f"RUNNER BLANKET(all)       mean={bl_m:6.2f} med={bl_med:6.2f}  delta={bl_m-base_m:+.2f}")

print("\n=== split: effect on HOT trips vs COLD trips (blanket runner) ===")
hot=[t for t in usable if t['heat']>=HEAT_THR]; cold=[t for t in usable if t['heat']<HEAT_THR]
for lbl,coh in [('HOT',hot),('COLD',cold)]:
    b,_=portfolio(sim_current,coh); r,_=portfolio(lambda t:sim_runner(t,False),coh)
    print(f"{lbl:5s} n={len(coh):3d}: current mean={b:6.2f} -> runner mean={r:6.2f}  delta={r-b:+.2f}")

print("\n=== 4-half OOS: regime-gated runner delta per quarter ===")
n=len(usable);q=n//4
for i in range(4):
    seg=usable[i*q:(i+1)*q if i<3 else n]
    b,_=portfolio(sim_current,seg); r,_=portfolio(lambda t:sim_runner(t,True),seg)
    nhot=sum(1 for t in seg if t['heat']>=HEAT_THR and t['peak']>=TP2)
    print(f"Q{i+1}: current={b:6.2f} regime-runner={r:6.2f} delta={r-b:+.2f} (hot TP2-hits n={nhot})")

# sensitivity of HEAT_THR & GIVEBACK
print("\n=== sensitivity (regime-gated delta) ===")
for ht in [0.12,0.16,0.20,0.24]:
    for gb in [5,8,12]:
        def f(t):
            hot=t['heat']>=ht
            if not hot: return t['ret']
            if t['peak']>=TP2: return max(TP2,t['peak']-gb)
            return t['ret']
        m,_=portfolio(f,usable)
        print(f"heat_thr={ht:.2f} giveback={gb:2d}: mean={m:6.2f} delta={m-base_m:+.2f}")
