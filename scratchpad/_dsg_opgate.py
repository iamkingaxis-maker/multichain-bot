import json
from datetime import datetime,timezone
recs=json.load(open('scratchpad/_dsg_recs2.json'))
recs.sort(key=lambda r:r['ts'])
N=len(recs)
POS=25.0

def split(rs): return [r for r in rs if r['peak']>=20],[r for r in rs if r['peak']<6]
def metrics(rs):
    n=len(rs)
    if n==0: return dict(n=0,wr=0,mean=0,net=0,extop2=float('nan'),nwin=0)
    wr=sum(1 for r in rs if r['pnl_pct']>0)/n
    mean=sum(r['pnl_pct'] for r in rs)/n
    net=sum(r['pnl_pct']/100.0*POS for r in rs)
    srt=sorted(r['pnl_pct'] for r in rs); ex=srt[:-2] if n>2 else []
    return dict(n=n,wr=wr,mean=mean,net=net,extop2=(ex[len(ex)//2] if ex else float('nan')),nwin=sum(1 for r in rs if r['peak']>=20))

# accel distribution winners vs losers
W,L=split(recs)
import statistics as st
def q(xs,p): xs=sorted(xs); return xs[int(len(xs)*p)]
print('accel percentiles: winners vs losers')
for p in [.1,.25,.5,.75,.9]:
    print(f'  p{int(p*100):02d} W={q([r["accel_c"] for r in W],p):7.2f}  L={q([r["accel_c"] for r in L],p):7.2f}')
print('nf60 percentiles winners vs losers')
for p in [.1,.25,.5,.75]:
    print(f'  p{int(p*100):02d} W={q([r["nf60"] for r in W],p):8.1f}  L={q([r["nf60"] for r in L],p):8.1f}')

# ---- OPERATIONAL GATE: skip decaying-flow loser signature ----
# accel defined = nf60/(nf5/5). Loser signature = flow decaying/weak.
# Gate: KEEP if accel >= A_MIN AND nf60 >= NF60_MIN. else skip.
def gate(r,amin,nfmin):
    return (r['accel_c']>=amin) and (r['nf60']>=nfmin)

print('\n=== OPERATIONAL ACCEL GATE sweep (keep if accel>=A and nf60>=NF) ===')
print(f'{"A_MIN":>6s} {"NF60":>6s} {"keep":>5s} {"thru%":>6s} {"wr%":>6s} {"mean%":>7s} {"net$":>8s} {"wkept":>6s} {"wskip":>6s} {"lavoid":>7s} {"extop2%":>8s}')
base=metrics(recs)
print(f'{"base":>6s} {"-":>6s} {base["n"]:5d} 100.0% {base["wr"]*100:5.1f}% {base["mean"]:6.2f}% {base["net"]:7.2f} {base["nwin"]:6d} {"-":>6s} {"-":>7s} {base["extop2"]:7.3f}%')
configs=[(1.0,0),(1.5,0),(2.0,0),(1.5,20),(2.0,20),(1.5,50),(2.0,50)]
for amin,nfmin in configs:
    kept=[r for r in recs if gate(r,amin,nfmin)]; skip=[r for r in recs if not gate(r,amin,nfmin)]
    m=metrics(kept); ws=sum(1 for r in skip if r['peak']>=20); la=sum(1 for r in skip if r['peak']<6)
    print(f'{amin:6.1f} {nfmin:6.0f} {m["n"]:5d} {m["n"]/N*100:5.1f}% {m["wr"]*100:5.1f}% {m["mean"]:6.2f}% {m["net"]:7.2f} {m["nwin"]:6d} {ws:6d} {la:7d} {m["extop2"]:7.3f}%')

# choose robust config: accel>=1.5 & nf60>=20 -> per-quarter
CHO=(1.5,20)
q=N//4
quarters=[recs[0:q],recs[q:2*q],recs[2*q:3*q],recs[3*q:]]
def lbl(rs):
    a=datetime.fromtimestamp(rs[0]['ts'],tz=timezone.utc).strftime('%m-%d %Hh')
    b=datetime.fromtimestamp(rs[-1]['ts'],tz=timezone.utc).strftime('%m-%d %Hh')
    return a+'->'+b
print(f'\n=== Per-quarter effect of chosen gate accel>=1.5 & nf60>=20 ===')
print(f'{"quarter":22s} {"base_net":>9s} {"gate_net":>9s} {"d_net":>7s} {"b_wr":>6s} {"g_wr":>6s} {"b_mean":>7s} {"g_mean":>7s} {"thru%":>6s}')
allpos=True
for i,qr in enumerate(quarters):
    b=metrics(qr); k=metrics([r for r in qr if gate(r,*CHO)])
    if k["net"]<b["net"]: allpos=False
    print(f'Q{i+1} {lbl(qr):18s} {b["net"]:9.2f} {k["net"]:9.2f} {k["net"]-b["net"]:7.2f} {b["wr"]*100:5.1f}% {k["wr"]*100:5.1f}% {b["mean"]:6.2f}% {k["mean"]:6.2f}% {k["n"]/b["n"]*100:5.1f}%')
print('gate improves net in ALL four quarters:', allpos)

# ex-top-2 net reconciliation for chosen gate
b=metrics(recs); k=metrics([r for r in recs if gate(r,*CHO)])
srt=sorted(recs,key=lambda r:r['pnl_pct'],reverse=True); t2=sum(r['pnl_pct']/100*POS for r in srt[:2])
srtk=sorted([r for r in recs if gate(r,*CHO)],key=lambda r:r['pnl_pct'],reverse=True); t2k=sum(r['pnl_pct']/100*POS for r in srtk[:2])
print(f'\n=== chosen gate ex-top-2 net reconciliation ===')
print(f'base net ${b["net"]:.2f} (ex-top2 ${b["net"]-t2:.2f}), gate net ${k["net"]:.2f} (ex-top2 ${k["net"]-t2k:.2f})')
print(f'improvement WITH fat tail: ${k["net"]-b["net"]:.2f} ; EX fat tail: ${(k["net"]-t2k)-(b["net"]-t2):.2f}')
print(f'ex-top2 MEDIAN base {b["extop2"]:.3f}% vs gate {k["extop2"]:.3f}% (unchanged: median pinned in loss-dominated pop)')
