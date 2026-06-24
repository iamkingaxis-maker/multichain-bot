"""Grid-search entry gates in SOL-pump window + broad-universe corroboration.
Confirm the 'shallow-pullback-in-uptrend, turning up' shape with numeric gates,
report n / WR / mean exit, and stress for thinness."""
from curl_cffi import requests as r
import statistics as st
import itertools

BASE='https://gracious-inspiration-production.up.railway.app'
d=r.get(BASE+'/api/universe-recorder?limit=80000', impersonate='chrome', timeout=120).json()
recs = d if isinstance(d,list) else d.get('events',[])
settled=[x for x in recs if isinstance(x.get('exit_pct'),(int,float))]
def num(x,k):
    v=x.get(k); return v if isinstance(v,(int,float)) else None

pump=[x for x in settled if (num(x,'sol_pc_h6') or -9)>=2.0]
# slightly looser pump proxy for power
pump_loose=[x for x in settled if (num(x,'sol_pc_h6') or -9)>=1.0]

def ev(rows):
    e=[x['exit_pct'] for x in rows]
    if not e: return (0,0,0,0,0)
    return (len(e), st.mean(e), st.median(e),
            sum(1 for v in e if v>0)/len(e), sum(1 for v in e if v>=5)/len(e))

def show(rows,label):
    n,m,md,wr,w5=ev(rows)
    print('  %-44s n=%4d mean=%+5.1f%% med=%+5.1f%% WR=%.0f%% hit+5=%.0f%%'%(label,n,m,md,wr*100,w5*100))

# The shape gate (from winner/loser separation):
# shallow drawdown from peak (>= -10), turning up last 5m (traj_price_5m_pct>0),
# established uptrend (pc_h6 high), young.
def shape(x, dd_min=-10, p5_min=0.0, h6_min=10, age_max=72):
    dd=num(x,'traj_drawdown_from_peak_pct'); p5=num(x,'traj_price_5m_pct'); h6=num(x,'pc_h6'); age=num(x,'age_hours')
    if None in (dd,p5,h6,age): return False
    return dd>=dd_min and p5>=p5_min and h6>=h6_min and age<=age_max

print('=== BASELINES ===')
show(pump,'PUMP window (sol_h6>=2) ALL')
show(pump_loose,'PUMP-loose (sol_h6>=1) ALL')
show(settled,'WHOLE universe ALL')

print('\n=== SHAPE GATE (shallow-dip-turning-up in uptrend) ===')
print('-- in PUMP window (sol_h6>=2):')
show([x for x in pump if shape(x)],'shape: dd>=-10 & p5>0 & h6>=10 & age<=72')
print('-- in PUMP-loose (sol_h6>=1):')
show([x for x in pump_loose if shape(x)],'shape: dd>=-10 & p5>0 & h6>=10 & age<=72')
print('-- in WHOLE universe (does the shape generalize?):')
show([x for x in settled if shape(x)],'shape all-regime')

print('\n=== COMPONENT ABLATION (whole universe, for power n) ===')
show([x for x in settled if (num(x,'traj_price_5m_pct') or -9)>0],'turning-up: traj_price_5m_pct>0 ONLY')
show([x for x in settled if (num(x,'traj_drawdown_from_peak_pct') or -99)>=-10],'shallow: dd_from_peak>=-10 ONLY')
show([x for x in settled if (num(x,'pc_h6') or -9)>=10],'uptrend: pc_h6>=10 ONLY')
show([x for x in settled if (num(x,'age_hours') or 1e9)<=72],'young: age<=72 ONLY')
show([x for x in settled if (num(x,'pc_h6') or -9)>=10 and (num(x,'traj_price_5m_pct') or -9)>0],'h6>=10 & p5>0')
show([x for x in settled if (num(x,'pc_h6') or -9)>=10 and (num(x,'traj_drawdown_from_peak_pct') or -99)>=-8],'h6>=10 & dd>=-8 (shallow near high)')

print('\n=== CONTRAST: deep-dip (fleet thesis) in pump window ===')
show([x for x in pump if (num(x,'traj_drawdown_from_peak_pct') or 0)<=-20],'deep dd<=-20 PUMP')
show([x for x in settled if (num(x,'traj_drawdown_from_peak_pct') or 0)<=-20],'deep dd<=-20 ALL')

print('\n=== threshold sweep on traj_drawdown (whole universe, with h6>=10 & p5>0) ===')
base=[x for x in settled if (num(x,'pc_h6') or -9)>=10 and (num(x,'traj_price_5m_pct') or -9)>0]
for ddm in [-4,-6,-8,-10,-15,-20,-30]:
    show([x for x in base if (num(x,'traj_drawdown_from_peak_pct') or -99)>=ddm],'  +dd>=%d'%ddm)

print('\n=== threshold sweep on pc_h6 (whole universe, with dd>=-10 & p5>0) ===')
base2=[x for x in settled if (num(x,'traj_drawdown_from_peak_pct') or -99)>=-10 and (num(x,'traj_price_5m_pct') or -9)>0]
for h6m in [0,5,10,20,30,50]:
    show([x for x in base2 if (num(x,'pc_h6') or -99)>=h6m],'  +h6>=%d'%h6m)
