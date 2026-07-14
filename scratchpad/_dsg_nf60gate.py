import json
from datetime import datetime,timezone
recs=json.load(open('scratchpad/_dsg_recs2.json'))
recs.sort(key=lambda r:r['ts'])
N=len(recs); POS=25.0

def split(rs): return [r for r in rs if r['peak']>=20],[r for r in rs if r['peak']<6]
def metrics(rs):
    n=len(rs)
    if n==0: return dict(n=0,wr=0,mean=0,net=0,extop2=float('nan'),nwin=0)
    wr=sum(1 for r in rs if r['pnl_pct']>0)/n
    mean=sum(r['pnl_pct'] for r in rs)/n
    net=sum(r['pnl_pct']/100.0*POS for r in rs)
    srt=sorted(r['pnl_pct'] for r in rs); ex=srt[:-2] if n>2 else []
    return dict(n=n,wr=wr,mean=mean,net=net,extop2=(ex[len(ex)//2] if ex else float('nan')),nwin=sum(1 for r in rs if r['peak']>=20))
def auc(pos,neg):
    pos=[x for x in pos]; neg=[x for x in neg]
    if not pos or not neg: return float('nan')
    allv=sorted([(v,0) for v in neg]+[(v,1) for v in pos]); vals=[v for v,_ in allv]; n=len(vals); ranks=[0.0]*n; i=0
    while i<n:
        j=i
        while j<n and vals[j]==vals[i]: j+=1
        r=(i+1+j)/2.0
        for k in range(i,j): ranks[k]=r
        i=j
    sp=sum(ranks[k] for k in range(n) if allv[k][1]==1); npos=len(pos); nneg=len(neg)
    return (sp-npos*(npos+1)/2.0)/(npos*nneg)

q=N//4
quarters=[recs[0:q],recs[q:2*q],recs[2*q:3*q],recs[3*q:]]
def lbl(rs):
    a=datetime.fromtimestamp(rs[0]['ts'],tz=timezone.utc).strftime('%m-%d %Hh'); b=datetime.fromtimestamp(rs[-1]['ts'],tz=timezone.utc).strftime('%m-%d %Hh'); return a+'->'+b

base=metrics(recs)
print(f'BASE n={base["n"]} wr={base["wr"]*100:.1f}% mean={base["mean"]:.2f}% net=${base["net"]:.2f} winners={base["nwin"]} extop2={base["extop2"]:.3f}%')
print('\n=== CONFIG-EXPRESSIBLE GATE: net_flow_60s_usd >= NF60  [ + net_flow_5m_usd >= NF5 ] ===')
print(f'{"NF60":>6s} {"NF5":>6s} {"keep":>5s} {"thru%":>6s} {"wr%":>6s} {"mean%":>7s} {"net$":>8s} {"netpos":>7s} {"wkept":>6s} {"wskip":>6s} {"lavoid":>7s} {"extop2%":>8s}')
for nf60,nf5 in [(0,None),(20,None),(50,None),(100,None),(50,50),(100,50),(50,100)]:
    def keep(r):
        if r['nf60']<nf60: return False
        if nf5 is not None and r['nf5']<nf5: return False
        return True
    kept=[r for r in recs if keep(r)]; skip=[r for r in recs if not keep(r)]
    m=metrics(kept); ws=sum(1 for r in skip if r['peak']>=20); la=sum(1 for r in skip if r['peak']<6)
    npos=m['net']/m['n'] if m['n'] else 0
    print(f'{nf60:6.0f} {str(nf5):>6s} {m["n"]:5d} {m["n"]/N*100:5.1f}% {m["wr"]*100:5.1f}% {m["mean"]:6.2f}% {m["net"]:7.2f} {npos:7.3f} {m["nwin"]:6d} {ws:6d} {la:7d} {m["extop2"]:7.3f}%')

# choose nf60>=50 as clean config gate; per-quarter robustness + AUC
NF60=50.0
print(f'\n=== Per-quarter: gate net_flow_60s_usd >= {NF60:.0f} ===')
print(f'{"quarter":22s} {"base_net":>9s} {"gate_net":>9s} {"d_net":>7s} {"b_wr":>6s} {"g_wr":>6s} {"thru%":>6s} {"nf60_AUC":>8s}')
allpos=True
for i,qr in enumerate(quarters):
    b=metrics(qr); k=metrics([r for r in qr if r['nf60']>=NF60])
    Wq,Lq=split(qr); a=auc([r['nf60'] for r in Wq],[r['nf60'] for r in Lq])
    if k['net']<b['net']: allpos=False
    print(f'Q{i+1} {lbl(qr):18s} {b["net"]:9.2f} {k["net"]:9.2f} {k["net"]-b["net"]:7.2f} {b["wr"]*100:5.1f}% {k["wr"]*100:5.1f}% {k["n"]/b["n"]*100:5.1f}% {a:8.3f}')
print('gate improves net in ALL four quarters:', allpos)

# ex-top-2 reconciliation for nf60>=50
b=metrics(recs); k=metrics([r for r in recs if r['nf60']>=NF60])
srt=sorted(recs,key=lambda r:r['pnl_pct'],reverse=True); t2=sum(r['pnl_pct']/100*POS for r in srt[:2])
srtk=sorted([r for r in recs if r['nf60']>=NF60],key=lambda r:r['pnl_pct'],reverse=True); t2k=sum(r['pnl_pct']/100*POS for r in srtk[:2])
print(f'\nex-top-2: base net ${b["net"]:.2f} (ex ${b["net"]-t2:.2f}) -> gate net ${k["net"]:.2f} (ex ${k["net"]-t2k:.2f})')
print(f'net improvement WITH fat tail ${k["net"]-b["net"]:.2f} ; EX fat tail ${(k["net"]-t2k)-(b["net"]-t2):.2f}')
print(f'ex-top2 median base {b["extop2"]:.3f}% -> gate {k["extop2"]:.3f}%')
