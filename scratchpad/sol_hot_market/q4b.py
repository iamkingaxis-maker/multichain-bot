import json, statistics as st
from datetime import datetime
d=json.load(open('scratchpad/sol_selection/_trips.json'))
for t in d: t['dt']=datetime.fromisoformat(t['time'])
d.sort(key=lambda t:t['dt'])
def mean(xs): return st.mean(xs) if xs else float('nan')
K=25
for i,t in enumerate(d):
    prev=d[max(0,i-K):i]
    t['heat']=(sum(1 for p in prev if p['peak']>=20)/len(prev)) if len(prev)>=10 else None
usable=[t for t in d if t['heat'] is not None]
HEAT_THR=0.20

# Model a synthetic exit from peak/mae so TP1 vs TP2 changes are comparable across policies.
# exit_ret(TP1,TP2, trail): sell half at TP1, half at TP2; if never TP2, trail remainder.
STOP=-10.0
def exit_ret(t, tp1, tp2):
    pk=t['peak']; mae=t['mae'] if t.get('mae') is not None else -8.0
    if pk < tp1:            # never hit first target -> exits near realized/stop
        return max(mae, STOP) if pk<=0 else t['ret']
    if pk < tp2:            # hit TP1 not TP2: half@tp1, half exits at realized-ish (use min(pk, ret? ) ) approx half@tp1 half@ (pk*0.5)
        return 0.5*tp1 + 0.5*max(STOP, pk-6)
    return 0.5*tp1 + 0.5*tp2   # both targets

# Baseline current policy TP1=6 TP2=12
def cur(t): return exit_ret(t,6,12)
# Policy A: raise TP1 blanket 6->10
def a_blanket(t): return exit_ret(t,10,12)
# Policy A regime: raise TP1 only in HIGH heat
def a_regime(t): return exit_ret(t,10,12) if t['heat']>=HEAT_THR else exit_ret(t,6,12)
# Policy B: raise TP2 blanket 12->20
def b_blanket(t): return exit_ret(t,6,20)
# Policy B regime: raise TP2 only in HIGH heat
def b_regime(t): return exit_ret(t,6,20) if t['heat']>=HEAT_THR else exit_ret(t,6,12)

def pm(fn,coh): return mean([fn(t) for t in coh])
base=pm(cur,usable)
print(f"synthetic baseline mean = {base:.2f}")
for lbl,fn in [('A raise TP1->10 BLANKET',a_blanket),('A raise TP1->10 REGIME',a_regime),
               ('B raise TP2->20 BLANKET',b_blanket),('B raise TP2->20 REGIME',b_regime)]:
    print(f"{lbl:28s} mean={pm(fn,usable):6.2f} delta={pm(fn,usable)-base:+.2f}")

print("\n--- effect split HOT vs COLD (synthetic) ---")
hot=[t for t in usable if t['heat']>=HEAT_THR]; cold=[t for t in usable if t['heat']<HEAT_THR]
for lbl,fn in [('A TP1->10',a_blanket),('B TP2->20',b_blanket)]:
    print(f"{lbl}:  HOT delta={pm(fn,hot)-pm(cur,hot):+.2f}   COLD delta={pm(fn,cold)-pm(cur,cold):+.2f}")

print("\n--- 4-half OOS: B raise TP2->20 REGIME delta ---")
n=len(usable);qn=n//4
for i in range(4):
    seg=usable[i*qn:(i+1)*qn if i<3 else n]
    print(f"Q{i+1}: delta={pm(b_regime,seg)-pm(cur,seg):+.2f}")
