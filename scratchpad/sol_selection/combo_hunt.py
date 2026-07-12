import json, statistics as st
from collections import defaultdict, Counter

T = [t for t in json.load(open('scratchpad/sol_selection/_trips.json')) if t.get('ret') is not None]
legc = Counter(t['token'] for t in T)
TOP2 = set(tok for tok,_ in legc.most_common(2))
N_ALL = len(T)
DEEP = lambda t: (t.get('pc_h1') or 0) <= -45.0
N_DEEP = sum(1 for t in T if DEEP(t))

def tokmed(trips):
    by=defaultdict(list)
    for t in trips:
        if t['token'] in TOP2: continue
        by[t['address']].append(t['ret'])
    per=[st.median(v) for v in by.values()]
    return (st.median(per), len(per)) if per else (None,0)
def wr(trips): return 100*sum(1 for t in trips if t['ret']>0)/len(trips) if trips else None
def p90(trips):
    r=sorted(t['ret'] for t in trips); return r[int(len(r)*0.9)] if r else None
def hour(t):
    try: return int((t['time'] or '')[11:13])
    except: return None
def splits():
    s=sorted(T,key=lambda t:t['sell_time'] or t['time'] or ''); mid=len(s)//2
    return {'CH1':s[:mid],'CH2':s[mid:],
            'ODD':[t for t in T if int((t['time'] or '2026-01-01')[8:10])%2==1],
            'EVEN':[t for t in T if int((t['time'] or '2026-01-01')[8:10])%2==0]}
def fmt(x): return f"{x:+.1f}" if isinstance(x,(int,float)) else "  -"

SPL = splits()
# winner-kill reference: deep-alone p90 per half + tokmed
def deep_only(sub): return [t for t in sub if DEEP(t)]

def grade(cond, label):
    """cond(t) -> bool; combo = DEEP and cond. Grade pass side across halves."""
    combo = lambda t: DEEP(t) and cond(t)
    n_combo = sum(1 for t in T if combo(t))
    vol_of_deep = n_combo / N_DEEP if N_DEEP else 0
    vol_of_all = n_combo / N_ALL
    pm_all, pn_all = tokmed([t for t in T if combo(t)])
    p90_all = p90([t for t in T if combo(t)])
    p90_deep_all = p90([t for t in T if DEEP(t)])
    green_halves = 0; half_rep=[]
    for name in ['CH1','CH2','ODD','EVEN']:
        sub=SPL[name]
        pv=[t for t in sub if combo(t)]
        pm,pn=tokmed(pv)
        # winner-kill vs deep-alone in same half
        dp=p90(deep_only(sub))
        cp=p90(pv)
        is_green = (pm is not None and pm >= 0)
        if is_green: green_halves+=1
        half_rep.append((name,pm,pn,cp,dp))
    # winner-kill across ALL: combo p90 vs deep-alone p90
    wk = None
    if p90_deep_all is not None and p90_deep_all!=0 and p90_all is not None:
        # fraction of the deep fat-tail retained; kill = how much below deep p90
        wk = (p90_deep_all - p90_all)  # positive = combo clips winners
    print(f"\n### {label}")
    print(f"    n_combo={n_combo}  vol_of_deep={vol_of_deep*100:.0f}%  vol_of_all={vol_of_all*100:.1f}%")
    print(f"    ALL pass ex2 tokmed={fmt(pm_all)} ({pn_all} tok)  p90={fmt(p90_all)} (deep-alone p90={fmt(p90_deep_all)}, kill={fmt(wk)})")
    for name,pm,pn,cp,dp in half_rep:
        g='GREEN' if (pm is not None and pm>=0) else 'red'
        print(f"    {name:<5} tokmed={fmt(pm):>7} ({pn:>2} tok) {g:<6} p90={fmt(cp):>6} (deep {fmt(dp)})")
    verdict = green_halves>=3 and vol_of_deep>=0.60
    print(f"    >> GREEN halves={green_halves}/4  vol_of_deep>=60%={vol_of_deep>=0.60}  ==> SHIP={verdict}")
    return dict(label=label,n=n_combo,vol_of_deep=vol_of_deep,vol_of_all=vol_of_all,
                all_tm=pm_all,green_halves=green_halves,ship=verdict,wk=wk)

print(f"BASELINE  N_ALL={N_ALL}  N_DEEP={N_DEEP} ({N_DEEP/N_ALL*100:.1f}% of fills)")
grade(lambda t: True, 'DEEP-ALONE (reference)')

R=[]
# ---- LIQ band ----
R.append(grade(lambda t: (t.get('liq') or 0)>=30000, 'DEEP + liq>=30k'))
R.append(grade(lambda t: (t.get('liq') or 0)>=35000, 'DEEP + liq>=35k'))
R.append(grade(lambda t: (t.get('liq') or 1e18)<=35000, 'DEEP + liq<=35k (thin)'))
# ---- proven pre-entry volume (RH axis, on Solana) ----
R.append(grade(lambda t: (t.get('entry_vol_h24') or 0)>=700000, 'DEEP + entry_vol_h24>=700k'))
R.append(grade(lambda t: (t.get('entry_vol_h24') or 0)>=1000000, 'DEEP + entry_vol_h24>=1M'))
R.append(grade(lambda t: (t.get('entry_vol_h24') or 0)>=1500000, 'DEEP + entry_vol_h24>=1.5M'))
R.append(grade(lambda t: (t.get('rt_buys_usd') or 0)>=2500, 'DEEP + rt_buys_usd>=2500'))
# ---- demand composition ----
R.append(grade(lambda t: (t.get('unique_buyers_n') or 0)>=40, 'DEEP + unique_buyers>=40'))
R.append(grade(lambda t: (t.get('unique_buyers_n') or 0)>=45, 'DEEP + unique_buyers>=45'))
R.append(grade(lambda t: (t.get('net_flow_15s') or -1e18)>=100, 'DEEP + net_flow_15s>=100 (buyers stepping in)'))
R.append(grade(lambda t: (t.get('net_flow_15s') or -1e18)>=200, 'DEEP + net_flow_15s>=200'))
R.append(grade(lambda t: (t.get('net_flow_60s') or -1e18)>=0, 'DEEP + net_flow_60s>=0'))
R.append(grade(lambda t: (t.get('bs_h1') or 0)>=1.3, 'DEEP + bs_h1>=1.3 (buy-skew 1h)'))
R.append(grade(lambda t: (t.get('bs_h1') or 1e18)<=1.4, 'DEEP + bs_h1<=1.4 (not over-bought)'))
R.append(grade(lambda t: (t.get('buy_pressure_60s') or 0)>=0.55, 'DEEP + buy_pressure_60s>=0.55'))
# ---- rug / supply ----
R.append(grade(lambda t: (t.get('top10_holder_pct') or 1e18)<=40, 'DEEP + top10_holder<=40% (distributed)'))
R.append(grade(lambda t: (t.get('top10_holder_pct') or 1e18)<=30, 'DEEP + top10_holder<=30%'))
# ---- hour: 03-08 UTC young window (now open, was best) ----
R.append(grade(lambda t: (hour(t) is not None and 3<=hour(t)<8), 'DEEP + hour 03-08 UTC'))
R.append(grade(lambda t: (hour(t) is not None and 13<=hour(t)<22), 'DEEP + hour 13-22 UTC (prime)'))
R.append(grade(lambda t: (hour(t) is not None and not (9<=hour(t)<13)), 'DEEP + NOT 09-13 (skip dead)'))

print("\n\n===== SHIPPABLE (>=3/4 green AND >=60% deep vol) =====")
for r in sorted(R,key=lambda x:(-x['green_halves'],-(x['all_tm'] or -99))):
    tag='**SHIP**' if r['ship'] else ('near' if r['green_halves']>=3 else '')
    print(f"  {r['label']:<45} green={r['green_halves']}/4  all_tm={fmt(r['all_tm'])}  vol_deep={r['vol_of_deep']*100:.0f}%  vol_all={r['vol_of_all']*100:.1f}%  {tag}")
