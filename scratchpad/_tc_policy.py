import json, statistics as st
P=json.load(open('scratchpad/_tc_positions.json'))
def g(p,f):
    v=p['em'].get(f); return v if isinstance(v,(int,float)) else None
def swing(p):
    tv=g(p,'token_volatility_h24_pct'); bo=g(p,'1m_body_pct_avg'); pc=g(p,'pc_h24')
    parts=[]; 
    for v,thr in [(tv,140),(bo,2.6),(pc,80)]:
        if v is not None: parts.append(1 if v>=thr else 0)
    return (sum(parts) if len(parts)>=2 else None)
R=[p for p in P if swing(p) is not None]
for p in R:
    p['_sw']=swing(p); p['_deep']=(g(p,'pc_h6') or 0)<=-40
    p['_violent_shallow']= p['_sw']>=2 and not p['_deep']
def sim(rows, sizer, name):
    tot=0; cap=0; risk=[]
    for p in rows:
        sz=sizer(p); cap+=sz; u=sz*p['pnl']/100.0; tot+=u; risk.append(u)
    risk.sort()
    print('%-30s totPnL=$%+.0f cap=$%.0f ROI=%+.2f%% worst=$%.1f p02=$%.1f p05=$%.1f'%(
        name,tot,cap,100*tot/cap,risk[0],risk[int(0.02*len(risk))],risk[int(0.05*len(risk))]))
n_vs=sum(1 for p in R if p['_violent_shallow'])
print('n=%d  violent_shallow=%d (%.0f%%)'%(len(R),n_vs,100*n_vs/len(R)))
sim(R, lambda p:100, 'UNIFORM $100')
sim(R, lambda p: 40 if p['_violent_shallow'] else 100, 'ADAPTIVE: vs=0.4x else 1.0x')
sim(R, lambda p: 0 if p['_violent_shallow'] else 100, 'ADAPTIVE-SKIP vs (block)')
sim(R, lambda p: 50 if p['_sw']>=2 else 100, 'crude: all-high=0.5x')
# EV of the blocked cohort:
vs=[p['pnl'] for p in R if p['_violent_shallow']]
print('violent_shallow cohort: EV=%+.2f WR=%.0f%% median=%.1f'%(st.mean(vs),100*sum(1 for x in vs if x>0)/len(vs),st.median(vs)))
