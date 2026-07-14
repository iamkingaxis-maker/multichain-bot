import json, statistics as st
P=json.load(open('scratchpad/_tc_positions.json'))
def g(p,f):
    v=p['em'].get(f); return v if isinstance(v,(int,float)) else None
def stats(rows,label):
    v=[p['pnl'] for p in rows]; vs=sorted(v)
    ev=st.mean(v); wr=100*sum(1 for x in v if x>0)/len(v)
    p05=vs[max(0,int(0.05*len(vs)))]; mn=vs[0]
    gap=100*sum(1 for x in v if x<-12)/len(v)
    print('%-16s n=%3d EV=%+.2f WR=%.0f%% gap%%=%.0f p05=%.1f min=%.1f'%(label,len(rows),ev,wr,gap,p05,mn))
    return dict(n=len(rows),ev=ev,wr=wr,p05=p05,mn=mn,gap=gap)

# Composite SWING score: z-ish using 3 reachable factors present-at-entry.
# use token_volatility_h24_pct, 1m_body_pct_avg, pc_h24 (blowoff). Normalize by median.
def swing(p):
    tv=g(p,'token_volatility_h24_pct'); bo=g(p,'1m_body_pct_avg'); pc=g(p,'pc_h24')
    parts=[]
    if tv is not None: parts.append(1 if tv>=140 else 0)
    if bo is not None: parts.append(1 if bo>=2.6 else 0)
    if pc is not None: parts.append(1 if pc>=80 else 0)
    return sum(parts), len(parts)
rows=[p for p in P if swing(p)[1]>=2]  # need >=2 of 3 factors known
print('positions with >=2 swing factors:',len(rows))
for p in rows: p['_sw']=swing(p)[0]
print('\n=== SWING SCORE (0=calm .. 3=violent) ===')
buckets={}
for s in [0,1,2,3]:
    r=[p for p in rows if p['_sw']==s]
    if r: buckets[s]=stats(r,'swing=%d'%s)
LOW=[p for p in rows if p['_sw']<=1]; HIGH=[p for p in rows if p['_sw']>=2]
print()
sl=stats(LOW,'LOW(0-1)'); sh=stats(HIGH,'HIGH(2-3)')

print('\n=== ADAPTIVE SIZE simulation (per $100 base bet) ===')
# uniform: everyone $100. adaptive: HIGH-swing sized to 0.5x, LOW-swing 1.0x (or 1.2x)
def sim(rows, sizer):
    tot=0; risk=[]; cap=0
    for p in rows:
        sz=sizer(p); cap+=sz
        pnl_usd=sz*p['pnl']/100.0
        tot+=pnl_usd; risk.append(pnl_usd)
    return tot, cap, sorted(risk)[0], sorted(risk)[int(0.02*len(risk))]
allr=rows
u_tot,u_cap,u_min,u_p02=sim(allr, lambda p:100)
a_tot,a_cap,a_min,a_p02=sim(allr, lambda p:50 if p['_sw']>=2 else 100)
a2_tot,a2_cap,a2_min,a2_p02=sim(allr, lambda p:40 if p['_sw']>=2 else (120 if p['_sw']==0 else 100))
print('UNIFORM   $100 all : totPnL=$%+.0f  capDeployed=$%.0f  ROI=%.2f%%  worst1=$%.1f  p02=$%.1f'%(u_tot,u_cap,100*u_tot/u_cap,u_min,u_p02))
print('ADAPTIVE  hi=0.5x  : totPnL=$%+.0f  capDeployed=$%.0f  ROI=%.2f%%  worst1=$%.1f  p02=$%.1f'%(a_tot,a_cap,100*a_tot/a_cap,a_min,a_p02))
print('ADAPTIVE2 hi.4/lo1.2: totPnL=$%+.0f capDeployed=$%.0f  ROI=%.2f%%  worst1=$%.1f  p02=$%.1f'%(a2_tot,a2_cap,100*a2_tot/a2_cap,a2_min,a2_p02))

print('\n=== ADAPTIVE CONFIRM proxy: on HIGH-swing, require deeper dip (pc_h6<=-40) ===')
hi=HIGH
hi_all=stats(hi,'HI all')
hi_conf=stats([p for p in hi if (g(p,'pc_h6') or 0)<=-40],'HI+dip<=-40')
hi_noconf=stats([p for p in hi if (g(p,'pc_h6') or 0)>-40],'HI dip>-40')
