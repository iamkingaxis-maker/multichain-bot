"""CLEAN entry-gate validation (entry-state features ONLY, no traj_* look-ahead).
SOL-pump / euphoric regime. Source: /api/universe-recorder forward outcomes."""
from curl_cffi import requests as r
import statistics as st

BASE='https://gracious-inspiration-production.up.railway.app'
d=r.get(BASE+'/api/universe-recorder?limit=80000', impersonate='chrome', timeout=120).json()
recs = d if isinstance(d,list) else d.get('events',[])
settled=[x for x in recs if isinstance(x.get('exit_pct'),(int,float))]
def num(x,k):
    v=x.get(k); return v if isinstance(v,(int,float)) else None

pump=[x for x in settled if (num(x,'sol_pc_h6') or -9)>=2.0]          # current regime, thin
pump1=[x for x in settled if (num(x,'sol_pc_h6') or -9)>=1.0]         # looser, more power
allr=settled

def ev(rows):
    e=[x['exit_pct'] for x in rows]
    if not e: return None
    pk=[num(x,'peak_pct') for x in rows]; pk=[p for p in pk if p is not None]
    return (len(e), st.mean(e), st.median(e),
            sum(1 for v in e if v>0)/len(e), sum(1 for v in e if v>=5)/len(e),
            st.mean(pk) if pk else 0)

def show(rows,label):
    g=ev(rows)
    if not g: print('  %-46s n=0'%label); return
    n,m,md,wr,w5,mp=g
    flag=' *' if (n>=15 and wr>=0.55 and m>0) else ''
    print('  %-46s n=%4d mean=%+5.1f%% med=%+5.1f%% WR=%.0f%% +5=%.0f%% pk=%+.0f%%%s'%(label,n,m,md,wr*100,w5*100,mp,flag))

# ===== The momentum-continuation gate (shallow pullback in strong uptrend, vol) =====
def momo_cont(x, h6_min, h1_lo, h1_hi, vacc_min, age_max):
    h6=num(x,'pc_h6'); h1=num(x,'pc_h1'); vacc=num(x,'vol_h1_accel_vs_h6'); age=num(x,'age_hours')
    if None in (h6,h1,age): return False
    if h6 < h6_min: return False
    if not (h1_lo <= h1 <= h1_hi): return False
    if age > age_max: return False
    if vacc_min is not None and (vacc is None or vacc < vacc_min): return False
    return True

print('=== BASELINES ===')
show(pump,'PUMP (sol_h6>=2) ALL'); show(pump1,'PUMP-loose (sol_h6>=1) ALL'); show(allr,'WHOLE ALL')

print('\n=== H6 UPTREND STRENGTH sweep (whole universe) ===')
for h6m in [0,10,20,30,50,100]:
    show([x for x in allr if (num(x,'pc_h6') or -9)>=h6m],'pc_h6>=%d'%h6m)

print('\n=== H6>=30 + h1 pullback band (whole universe) ===')
base=[x for x in allr if (num(x,'pc_h6') or -9)>=30]
show(base,'pc_h6>=30')
for lo,hi in [(-30,0),(-15,2),(-8,3),(-30,-2),(0,99)]:
    show([x for x in base if lo<=(num(x,'pc_h1') or -99)<=hi],'  +pc_h1 in [%d,%d]'%(lo,hi))

print('\n=== add volume accel to (h6>=30, h1 in [-15,3]) ===')
b2=[x for x in allr if (num(x,'pc_h6') or -9)>=30 and -15<=(num(x,'pc_h1') or -99)<=3]
show(b2,'h6>=30 & h1[-15,3]')
for vm in [0.5,0.7,1.0,1.5]:
    show([x for x in b2 if (num(x,'vol_h1_accel_vs_h6') or -9)>=vm],'  +vol_h1_accel>=%.1f'%vm)

print('\n=== add YOUNG age to (h6>=30, h1 in [-15,3], vacc>=0.7) ===')
b3=[x for x in allr if (num(x,'pc_h6') or -9)>=30 and -15<=(num(x,'pc_h1') or -99)<=3 and (num(x,'vol_h1_accel_vs_h6') or -9)>=0.7]
show(b3,'h6>=30 & h1[-15,3] & vacc>=0.7')
for am in [12,24,48,72]:
    show([x for x in b3 if (num(x,'age_hours') or 1e9)<=am],'  +age<=%dh'%am)

print('\n=== FINAL CANDIDATE GATE across regimes ===')
def final(x): return momo_cont(x, h6_min=30, h1_lo=-15, h1_hi=3, vacc_min=0.7, age_max=48)
show([x for x in allr if final(x)],'FINAL whole-universe')
show([x for x in pump1 if final(x)],'FINAL pump-loose(sol_h6>=1)')
show([x for x in pump if final(x)],'FINAL pump(sol_h6>=2)')

print('\n=== CONTRAST: fleet deep-dip thesis (deep red body / hard pullback) ===')
show([x for x in allr if (num(x,'pc_h6') or -9)>=30 and (num(x,'pc_h1') or 99)<=-20],'h6>=30 & h1<=-20 (deep dip in uptrend)')
show([x for x in allr if (num(x,'body_pct') or 0)<=-3],'body_pct<=-3 (big red flush) ALL')
show([x for x in allr if (num(x,'pc_h1') or 99)<=-15],'pc_h1<=-15 (deep hour dip) ALL')

print('\n=== SANITY: outlier check on FINAL gate (are wins a few phantoms?) ===')
fr=[x for x in allr if final(x)]
ex=sorted([x['exit_pct'] for x in fr])
print('  n=%d exit_pct sorted tails: low5=%s ... high5=%s'%(len(ex), [round(v,1) for v in ex[:5]], [round(v,1) for v in ex[-5:]]))
print('  mean WITH top2 removed: %+.1f%%'%(st.mean(ex[:-2]) if len(ex)>3 else 0))
toks=set(x.get('token_address') for x in fr)
print('  distinct tokens=%d (n trades=%d)'%(len(toks),len(fr)))
