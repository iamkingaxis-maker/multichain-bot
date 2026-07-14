import json, glob, math
from collections import defaultdict

# Build proxy-price episodes per token from on-chain tape.
# Proxy price: cumulative net USD flow drives a log-price random walk scaled by
# per-token liquidity proxy L (median trade notional * k). This is DIRECTIONAL
# (captures pumps/retraces) though not a true AMM price. We test the GEOMETRY
# claim: shallow retrace of a pump -> continuation (new high) vs top (lower-low).

files = glob.glob('ripday/live_tapes/tape_*.jsonl')
def parse_ts(s):
    # 2026-07-04T12:15:33+00:00
    from datetime import datetime
    return datetime.fromisoformat(s).timestamp()

def zigzag(P, T, thr=0.15):
    # additive-in-logP pivots; thr = frac move to confirm reversal
    piv=[(0,P[0],T[0])]
    direction=0; last_i=0; last_p=P[0]
    ext_i=0; ext_p=P[0]
    for i in range(1,len(P)):
        p=P[i]
        if direction>=0:
            if p>ext_p: ext_p=p; ext_i=i
            if ext_p-p >= thr:  # reversal down confirmed
                piv.append((ext_i,ext_p,T[ext_i])); direction=-1; ext_p=p; ext_i=i
        if direction<=0:
            if p<ext_p: ext_p=p; ext_i=i
            if p-ext_p >= thr:
                piv.append((ext_i,ext_p,T[ext_i])); direction=1; ext_p=p; ext_i=i
    return piv

episodes=[]
for f in files:
    rows=[]
    for line in open(f):
        try: r=json.loads(line)
        except: continue
        rows.append(r)
    if len(rows)<80: continue
    rows.sort(key=lambda r:r['ts'])
    T=[parse_ts(r['ts']) for r in rows]
    sizes=[abs(r['volume_usd']) for r in rows if abs(r['volume_usd'])>0]
    if not sizes: continue
    med=sorted(sizes)[len(sizes)//2]
    L=max(med*40,500.0)   # liquidity proxy scale for logP
    # cumulative signed flow -> logP
    logP=[]; c=0.0
    for r in rows:
        s=r['volume_usd']*(1 if r['kind']=='buy' else -1)
        c+=s; logP.append(c/L)
    piv=zigzag(logP,T,thr=0.20)  # 20% logP move ~ pivot
    # find L0->H->L1 triples where H is a local max between two local mins
    # pivots alternate; identify min,max,min
    for k in range(len(piv)-2):
        (i0,p0,t0),(iH,pH,tH),(i1,p1,t1)=piv[k],piv[k+1],piv[k+2]
        if not (p0<pH and p1<pH): continue  # H is the peak
        pump=pH-p0            # log pump size
        retr=pH-p1            # log retrace size
        if pump<0.25: continue
        depth_frac = retr/pump if pump>0 else 0  # fraction of pump given back
        # higher-low proxy: L1 above base? p1-p0 in logP; >0 means higher-low
        hl = p1-p0
        # nf_imm: net USD flow in [t1, t1+30s]
        nf=0.0; makers=defaultdict(float)
        for j in range(i1, len(rows)):
            if T[j] > t1+30: break
            s=rows[j]['volume_usd']*(1 if rows[j]['kind']=='buy' else -1)
            nf+=s; makers[rows[j]['maker']]+=s
        # LABEL forward-only from L1: within 90min, new high(>pH)=CONT, else if
        # makes lower-low(<p1) first =TOP
        lab=None
        for j in range(i1+1,len(rows)):
            if T[j]>t1+5400: break
            if logP[j]>=pH: lab='CONT'; break
            if logP[j]<=p1-0.02: lab='TOP'; break
        if lab is None: continue
        episodes.append(dict(tok=f.split('_')[-1][:8], depth=depth_frac, hl=hl,
            nf=nf, makers=dict(makers), lab=lab, pump=pump, i1=i1, tokfile=f))

print("episodes",len(episodes),"tokens",len(set(e['tok'] for e in episodes)))
cont=sum(1 for e in episodes if e['lab']=='CONT')
print("base CONT rate %.3f"%(cont/len(episodes)))

def rate(sub):
    if not sub: return (0,0)
    return (sum(1 for e in sub if e['lab']=='CONT')/len(sub), len(sub))

# depth buckets (fraction of pump retraced)
for lo,hi in [(0,.4),(.4,.6),(.6,.8),(.8,1.2)]:
    sub=[e for e in episodes if lo<=e['depth']<hi]
    r,n=rate(sub); print(f"depth {lo}-{hi}: CONT {r:.3f} n={n}")
print("--- higher-low proxy hl>0 vs <=0 ---")
for cond,name in [(lambda e:e['hl']>0,'hl>0'),(lambda e:e['hl']<=0,'hl<=0')]:
    sub=[e for e in episodes if cond(e)]; r,n=rate(sub); print(name,f"CONT {r:.3f} n={n}")
print("--- nf_imm>0 vs <=0 ---")
for cond,name in [(lambda e:e['nf']>0,'nf>0'),(lambda e:e['nf']<=0,'nf<=0')]:
    sub=[e for e in episodes if cond(e)]; r,n=rate(sub); print(name,f"CONT {r:.3f} n={n}")
print("--- predicate shallow(depth<.5) & hl>0 & nf>0 ---")
sub=[e for e in episodes if e['depth']<.5 and e['hl']>0 and e['nf']>0]
r,n=rate(sub); print("predicate",f"CONT {r:.3f} n={n} coverage={n/len(episodes):.2f}")

json.dump(episodes, open('_adv_eps.json','w'))
