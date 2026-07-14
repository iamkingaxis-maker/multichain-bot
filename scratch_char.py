import json, statistics as st
from collections import defaultdict
d = json.load(open('_df_full.json.gz'))
buys=[x for x in d if x.get('type')=='buy']; sells=[x for x in d if x.get('type')=='sell']
sbk=defaultdict(list)
for s in sells: sbk[(s.get('address'),s.get('entry_price'))].append(s)
def rp(b):
    legs=sbk.get((b.get('address'),b.get('entry_price')))
    if not legs: return None
    num=den=0
    for s in legs:
        f=s.get('sell_fraction'); p=s.get('pnl_pct')
        if f is None or p is None: continue
        num+=f*p; den+=f
    return num/den if den>0 else None
P=[]
for b in buys:
    em=b.get('entry_meta');
    if not isinstance(em,dict): continue
    p=rp(b)
    if p is None: continue
    P.append((em,p))
def sf(em):
    return em.get('filter_falling_knife_verdict')=='BLOCK' or (em.get('1m_consec_red') or 0)>=3
inc=[(em,p) for em,p in P if em.get('filter_mtf_strong_downtrend_verdict')=='BLOCK' and not sf(em)]
print("INCREMENTAL-ONLY cohort n=",len(inc))
def desc(vals,lbl):
    vals=[v for v in vals if v is not None]
    if not vals: print(lbl,"none"); return
    print(f"{lbl}: median={st.median(vals):.2f} mean={sum(vals)/len(vals):.2f} min={min(vals):.2f} max={max(vals):.2f} n={len(vals)}")
desc([em.get('chart_mtf_score') for em,_ in inc],"mtf_score")
desc([em.get('1m_last_close_pct') for em,_ in inc],"1m_last_close_pct")
desc([em.get('pc_h1') for em,_ in inc],"pc_h1")
desc([em.get('chart_score') for em,_ in inc],"chart_score")
desc([em.get('1m_consec_red') for em,_ in inc],"1m_consec_red")
# last bar green/flat fraction
lc=[em.get('1m_last_close_pct') for em,_ in inc if em.get('1m_last_close_pct') is not None]
print("last bar >=0 (green/flat) frac:", round(sum(1 for x in lc if x>=0)/len(lc)*100,1),"%")
# pnl percentiles for incremental
pn=sorted(p for _,p in inc)
import numpy as np
print("inc pnl pcts 10/25/50/75/90:", [round(np.percentile(pn,q),2) for q in (10,25,50,75,90)])
