"""Characterize WINNING ENTRY SHAPE in the SOL-pump / euphoric regime.
Source: /api/universe-recorder (real fleet detections + 30m forward outcomes).
We slice to the current pump window (sol_pc_h6>=2) and compare DIP vs MOMENTUM
entry archetypes, then grid-search numeric thresholds for the best edge.
"""
from curl_cffi import requests as r
import json, statistics as st
import numpy as np

BASE='https://gracious-inspiration-production.up.railway.app'
d=r.get(BASE+'/api/universe-recorder?limit=80000', impersonate='chrome', timeout=120).json()
recs = d if isinstance(d,list) else d.get('events',[])
settled=[x for x in recs if isinstance(x.get('exit_pct'),(int,float))]
print('settled records:', len(settled))

def num(x,k):
    v=x.get(k)
    return v if isinstance(v,(int,float)) else None

def summ(rows, label):
    e=[x['exit_pct'] for x in rows]
    if len(e)<5:
        print('  %-30s n=%d (thin)'%(label,len(e))); return None
    pk=[num(x,'peak_pct') for x in rows]; pk=[p for p in pk if p is not None]
    wr=sum(1 for v in e if v>0)/len(e)
    w5=sum(1 for v in e if v>=5)/len(e)
    rt=sum(1 for v in e if v<=-5)/len(e)
    print('  %-30s n=%4d | mean=%+5.1f%% med=%+5.1f%% | WR=%.0f%% hit+5=%.0f%% rt<=-5=%.0f%% | meanpeak=%+.0f%%'%(
        label, len(e), st.mean(e), st.median(e), wr*100, w5*100, rt*100, (st.mean(pk) if pk else 0)))
    return dict(n=len(e),mean=st.mean(e),med=st.median(e),wr=wr,w5=w5)

# ---- Regime slices ----
pump = [x for x in settled if (num(x,'sol_pc_h6') or -9) >= 2.0]
calm = [x for x in settled if abs(num(x,'sol_pc_h6') or 0) < 1.0]
print('\n=== BASELINE BY REGIME ===')
summ(settled, 'ALL settled')
summ(pump, 'SOL-PUMP (sol_h6>=2)')
summ(calm, 'CALM (|sol_h6|<1)')

# ---- Entry archetype definitions ----
# DIP: token pulled back recently (pc_m5 negative / cum_pct_5m negative) but token strong on h1
# MOMENTUM/BREAKOUT: token UP on short windows (pc_m5>0, pc_h1>0), buy-side flow, near recent high
def is_dip(x):
    c5=num(x,'cum_pct_5m'); pm5=num(x,'pc_m5'); h1=num(x,'pc_h1')
    if c5 is None or h1 is None: return False
    return c5 <= -5 and h1 >= 0          # short-term dip within a not-down h1
def is_momo(x):
    pm5=num(x,'pc_m5'); h1=num(x,'pc_h1'); c5=num(x,'cum_pct_5m')
    if pm5 is None or h1 is None: return False
    return pm5 >= 2 and h1 >= 5 and (c5 is None or c5 >= -3)   # up now + up on hour, not pulling back
def near_high(x):
    dd=num(x,'traj_drawdown_from_peak_pct')
    return dd is not None and dd >= -6

print('\n=== ENTRY ARCHETYPE in SOL-PUMP window (n_pump=%d) ===' % len(pump))
summ([x for x in pump if is_dip(x)], 'DIP (cum5<=-5 & h1>=0)')
summ([x for x in pump if is_momo(x)], 'MOMENTUM (m5>=2 & h1>=5)')

print('\n=== same archetypes ALL regimes (for power) ===')
summ([x for x in settled if is_dip(x)], 'DIP all')
summ([x for x in settled if is_momo(x)], 'MOMENTUM all')

# ---- The proven calm-momentum predicate (calm_momentum_validate) on pump window ----
def calm_momo(x):
    h1=num(x,'pc_h1'); vm5=num(x,'vol_m5'); c5=num(x,'cum_pct_5m'); liq=num(x,'liq_usd')
    return (h1 is not None and h1>=13) and (vm5 is not None and vm5<=1510) and (c5 is not None and c5>=-7) and (liq is not None and liq>=50000)
print('\n=== proven calm-momentum predicate (pc_h1>=13 & vol_m5<=1510 & cum5>=-7 & liq>=50k) ===')
summ([x for x in settled if calm_momo(x)], 'calm-momo ALL')
summ([x for x in pump if calm_momo(x)], 'calm-momo PUMP-window')
