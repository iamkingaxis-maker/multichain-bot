import json, math, bisect
from datetime import datetime,timezone
recs=json.load(open('scratchpad/_dsg_recs2.json'))
recs.sort(key=lambda r:r['ts'])
N=len(recs)

def auc(pos,neg):
    pos=[x for x in pos if x is not None]; neg=[x for x in neg if x is not None]
    if not pos or not neg: return float('nan')
    allv=sorted([(v,0) for v in neg]+[(v,1) for v in pos])
    vals=[v for v,_ in allv]; n=len(vals); ranks=[0.0]*n; i=0
    while i<n:
        j=i
        while j<n and vals[j]==vals[i]: j+=1
        r=(i+1+j)/2.0
        for k in range(i,j): ranks[k]=r
        i=j
    sp=sum(ranks[k] for k in range(n) if allv[k][1]==1)
    npos=len(pos); nneg=len(neg); U=sp-npos*(npos+1)/2.0
    return U/(npos*nneg)
def split(rs): return [r for r in rs if r['peak']>=20],[r for r in rs if r['peak']<6]
def mk(vals):
    s=sorted(v for v in vals if v is not None)
    return lambda x: (bisect.bisect_right(s,x)/len(s)) if (x is not None and s) else 0.5

# ROBUST score: acceleration + nf60 (drop regime-unstable imbalance)
ra=mk([r['accel_c'] for r in recs]); rn=mk([r['nf60'] for r in recs])
for r in recs:
    r['rscore']=0.6*ra(r['accel_c'])+0.4*rn(r['nf60'])
    r['accel_only']=ra(r['accel_c'])

q=N//4
quarters=[recs[0:q],recs[q:2*q],recs[2*q:3*q],recs[3*q:]]
def lbl(rs):
    a=datetime.fromtimestamp(rs[0]['ts'],tz=timezone.utc).strftime('%m-%d %Hh')
    b=datetime.fromtimestamp(rs[-1]['ts'],tz=timezone.utc).strftime('%m-%d %Hh')
    return a+'->'+b

print('=== ROBUST score (0.6*rank(accel)+0.4*rank(nf60)) AUC per quarter ===')
for i,qr in enumerate(quarters):
    Wq,Lq=split(qr)
    print(f'Q{i+1} {lbl(qr):20s} n={len(qr)} W={len(Wq)} L={len(Lq)} '
          f'rscore={auc([r["rscore"] for r in Wq],[r["rscore"] for r in Lq]):.3f} '
          f'accel_only={auc([r["accel_only"] for r in Wq],[r["accel_only"] for r in Lq]):.3f}')
Wa,La=split(recs)
print(f'OVERALL rscore AUC={auc([r["rscore"] for r in Wa],[r["rscore"] for r in La]):.3f} '
      f'accel_only={auc([r["accel_only"] for r in Wa],[r["accel_only"] for r in La]):.3f}')

# ===== GATE using rscore, per-quarter net robustness =====
POS=25.0
def metrics(rs):
    n=len(rs)
    if n==0: return dict(n=0,wr=0,mean=0,net=0,extop2=float('nan'),nwin=0)
    wr=sum(1 for r in rs if r['pnl_pct']>0)/n
    mean=sum(r['pnl_pct'] for r in rs)/n
    net=sum(r['pnl_pct']/100.0*POS for r in rs)
    srt=sorted(r['pnl_pct'] for r in rs); ex=srt[:-2] if n>2 else []
    extop2=ex[len(ex)//2] if ex else float('nan')
    return dict(n=n,wr=wr,mean=mean,net=net,extop2=extop2,nwin=sum(1 for r in rs if r['peak']>=20))

scores=sorted(r['rscore'] for r in recs)
print('\n=== GATE (keep rscore>=thresh) — overall ===')
print(f'{"pct":>4s} {"keep":>5s} {"thru%":>6s} {"wr%":>6s} {"mean%":>7s} {"net$":>8s} {"net/pos":>8s} {"wkept":>6s} {"wskip":>6s} {"lavoid":>7s} {"extop2%":>8s}')
base=metrics(recs)
print(f'base {base["n"]:5d} 100.0% {base["wr"]*100:5.1f}% {base["mean"]:6.2f}% {base["net"]:7.2f} {base["net"]/base["n"]:8.3f} {base["nwin"]:6d} {0:6d} {0:7d} {base["extop2"]:7.3f}%')
chosen=None
for pct in [20,30,40,50]:
    thr=scores[int(len(scores)*pct/100)]
    kept=[r for r in recs if r['rscore']>=thr]; skip=[r for r in recs if r['rscore']<thr]
    m=metrics(kept)
    ws=sum(1 for r in skip if r['peak']>=20); la=sum(1 for r in skip if r['peak']<6)
    print(f'{pct:3d}% {m["n"]:5d} {m["n"]/N*100:5.1f}% {m["wr"]*100:5.1f}% {m["mean"]:6.2f}% {m["net"]:7.2f} {m["net"]/m["n"]:8.3f} {m["nwin"]:6d} {ws:6d} {la:7d} {m["extop2"]:7.3f}%')
    if pct==30: chosen=thr

# per-quarter net effect at chosen threshold (30th pct)
print(f'\n=== Per-quarter GATE net effect at 30th-pctile rscore (thr={chosen:.3f}) ===')
print(f'{"quarter":22s} {"base_net":>9s} {"gate_net":>9s} {"d_net":>8s} {"base_wr":>8s} {"gate_wr":>8s} {"thru%":>6s}')
for i,qr in enumerate(quarters):
    b=metrics(qr); k=metrics([r for r in qr if r['rscore']>=chosen])
    print(f'Q{i+1} {lbl(qr):18s} {b["net"]:9.2f} {k["net"]:9.2f} {k["net"]-b["net"]:8.2f} '
          f'{b["wr"]*100:7.1f}% {k["wr"]*100:7.1f}% {k["n"]/b["n"]*100:5.1f}%')

# ===== ex-top-2 reconciliation deep dive =====
print('\n=== EX-TOP-2 RECONCILIATION ===')
b=metrics(recs); k=metrics([r for r in recs if r['rscore']>=chosen])
print(f'baseline: mean={b["mean"]:.2f}%  net=${b["net"]:.2f}  ex-top2_med={b["extop2"]:.3f}%')
print(f'gated30 : mean={k["mean"]:.2f}%  net=${k["net"]:.2f}  ex-top2_med={k["extop2"]:.3f}%')
# what fraction of net-$ comes from top-2?
srt=sorted(recs,key=lambda r:r['pnl_pct'],reverse=True)
top2_net=sum(r['pnl_pct']/100*POS for r in srt[:2])
print(f'top-2 positions contribute ${top2_net:.2f} of baseline net ${b["net"]:.2f}')
srt_k=sorted([r for r in recs if r['rscore']>=chosen],key=lambda r:r['pnl_pct'],reverse=True)
top2_net_k=sum(r['pnl_pct']/100*POS for r in srt_k[:2])
print(f'gated top-2 contribute ${top2_net_k:.2f} of gated net ${k["net"]:.2f}')
print(f'baseline net EX top-2 = ${b["net"]-top2_net:.2f} ; gated net EX top-2 = ${k["net"]-top2_net_k:.2f}')
