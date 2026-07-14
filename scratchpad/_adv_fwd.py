import json, glob
from collections import defaultdict
from datetime import datetime
def pts(s): return datetime.fromisoformat(s).timestamp()
files=glob.glob('ripday/live_tapes/tape_*.jsonl')

# FORWARD-ONLY: no zigzag (which needs the future bounce to confirm L1).
# Causal algo, exactly what a bot can do streaming:
#  - track trailing max logP since a base -> that's running peak H*
#  - after a pump (H*-base>=0.25), watch retrace; maintain running low Lr
#  - TRIGGER when price bounces >=B off Lr (causal bounce). At trigger:
#       depth_frac = (H*-Lr)/(H*-base)   [known now]
#       hl = Lr-base                     [known now]
#       nf = net USD flow in the 30s BEFORE trigger ending now [causal]
#  - LABEL forward from trigger: new high>=H* within 90min = CONT,
#       else lower-low < Lr first = TOP.
def run(drop_top=0):
    eps=[]
    for f in files:
        rows=[json.loads(l) for l in open(f) if l.strip()]
        if len(rows)<80: continue
        rows.sort(key=lambda r:r['ts'])
        T=[pts(r['ts']) for r in rows]
        sizes=sorted(abs(r['volume_usd']) for r in rows if abs(r['volume_usd'])>0)
        if not sizes: continue
        med=sizes[len(sizes)//2]; L=max(med*40,500.0)
        # optionally drop top-N makers by gross volume (whale artifact test)
        if drop_top>0:
            gv=defaultdict(float)
            for r in rows: gv[r['maker']]+=abs(r['volume_usd'])
            banned=set(sorted(gv,key=gv.get,reverse=True)[:drop_top])
        else: banned=set()
        logP=[]; c=0.0
        for r in rows:
            if r['maker'] in banned:
                logP.append(c/L); continue
            c+=r['volume_usd']*(1 if r['kind']=='buy' else -1); logP.append(c/L)
        n=len(rows); i=0
        base=logP[0]; base_i=0; Hs=logP[0]; Hs_i=0; state='ACC'; Lr=None; Lr_i=None
        armed=[]
        for i in range(1,n):
            p=logP[i]
            if state=='ACC':
                if p>Hs: Hs=p; Hs_i=i
                if p<base: base=p; base_i=i; Hs=p; Hs_i=i
                if Hs-base>=0.25:
                    state='RETR'; Lr=p; Lr_i=i
            elif state=='RETR':
                if p>Hs: # new high before any trigger: pump extends, reset base higher
                    Hs=p; Hs_i=i; # keep tracking; no retrace yet
                    Lr=p; Lr_i=i
                    continue
                if p<Lr: Lr=p; Lr_i=i
                if p-Lr>=0.06 and Hs-Lr>0:  # causal bounce confirm (6% logP)
                    pump=Hs-base; depth=(Hs-Lr)/pump if pump>0 else 0; hl=Lr-base
                    # causal nf: 30s ending at trigger time
                    t=T[i]; nf=0.0
                    for j in range(i,-1,-1):
                        if T[j]<t-30: break
                        if rows[j]['maker'] in banned: continue
                        nf+=rows[j]['volume_usd']*(1 if rows[j]['kind']=='buy' else -1)
                    # forward label from trigger i
                    lab=None
                    for j in range(i+1,n):
                        if T[j]>t+5400: break
                        if logP[j]>=Hs: lab='CONT'; break
                        if logP[j]<=Lr-0.02: lab='TOP'; break
                    if lab:
                        eps.append(dict(tok=f.split('_')[-1][:8],depth=depth,hl=hl,nf=nf,lab=lab))
                    # after firing, rebase to look for next episode from here
                    state='ACC'; base=Lr; base_i=Lr_i; Hs=p; Hs_i=i
        # done token
    return eps

def report(eps,tag):
    if not eps: print(tag,"no eps"); return
    b=sum(1 for e in eps if e['lab']=='CONT')/len(eps)
    print(f"\n[{tag}] eps={len(eps)} tokens={len(set(e['tok'] for e in eps))} base CONT={b:.3f}")
    for lo,hi in [(0,.4),(.4,.6),(.6,.8),(.8,1.2)]:
        s=[e for e in eps if lo<=e['depth']<hi]
        if s: print(f"  depth {lo}-{hi}: {sum(1 for e in s if e['lab']=='CONT')/len(s):.3f} n={len(s)}")
    for cond,nm in [(lambda e:e['nf']>0,'nf>0'),(lambda e:e['nf']<=0,'nf<=0')]:
        s=[e for e in eps if cond(e)]
        if s: print(f"  {nm}: {sum(1 for e in s if e['lab']=='CONT')/len(s):.3f} n={len(s)}")
    s=[e for e in eps if e['depth']<.5 and e['hl']>0 and e['nf']>0]
    if s: print(f"  PREDICATE shallow&hl&nf: {sum(1 for e in s if e['lab']=='CONT')/len(s):.3f} n={len(s)} cov={len(s)/len(eps):.2f}")

report(run(0),"FORWARD-ONLY full")
report(run(2),"FORWARD-ONLY drop top-2 makers/token")
report(run(5),"FORWARD-ONLY drop top-5 makers/token")
