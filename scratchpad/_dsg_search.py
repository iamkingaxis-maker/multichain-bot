import json
from datetime import datetime,timezone
recs=json.load(open('scratchpad/_dsg_recs2.json'))
recs.sort(key=lambda r:r['ts'])
N=len(recs); POS=25.0
q=N//4
quarters=[recs[0:q],recs[q:2*q],recs[2*q:3*q],recs[3*q:]]

def metrics(rs):
    n=len(rs)
    if n==0: return dict(n=0,wr=0,mean=0,net=0)
    return dict(n=n,wr=sum(1 for r in rs if r['pnl_pct']>0)/n,
                mean=sum(r['pnl_pct'] for r in rs)/n,
                net=sum(r['pnl_pct']/100.0*POS for r in rs),
                nwin=sum(1 for r in rs if r['peak']>=20))

# check available imbalance-family fields quickly (AUC)
def auc(pos,neg):
    pos=[x for x in pos if x is not None]; neg=[x for x in neg if x is not None]
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
W=[r for r in recs if r['peak']>=20]; L=[r for r in recs if r['peak']<6]
print('field AUCs (winner vs loser):')
for f in ['accel_c','nf60','nf5','imb15','imb']:
    print(f'  {f:8s} {auc([r[f] for r in W],[r[f] for r in L]):.3f}')

def evalgate(name,keep):
    base=metrics(recs); k=metrics([r for r in recs if keep(r)])
    dq=[]
    allpos=True
    for qr in quarters:
        b=metrics(qr); g=metrics([r for r in qr if keep(r)])
        d=g['net']-b['net']; dq.append(d)
        if d<0: allpos=False
    print(f'{name:34s} thru={k["n"]/N*100:4.1f}% wr={k["wr"]*100:4.1f}% mean={k["mean"]:5.2f}% net=${k["net"]:7.2f} '
          f'dQ=[{dq[0]:6.1f},{dq[1]:6.1f},{dq[2]:6.1f},{dq[3]:6.1f}] allpos={allpos}')

print(f'\nBASELINE net=${metrics(recs)["net"]:.2f} thru=100%')
print('\n=== config-expressible gates (field floors), per-quarter dNet ===')
# acceleration reference (NOT config-expressible, shown for comparison)
evalgate('REF accel>=1.5 & nf60>=20', lambda r: r['accel_c']>=1.5 and r['nf60']>=20)
evalgate('REF accel>=2.0', lambda r: r['accel_c']>=2.0)
# config-expressible candidates
evalgate('nf60>=100 & nf5>=50', lambda r: r['nf60']>=100 and r['nf5']>=50)
evalgate('nf60>=100 & nf5>=25', lambda r: r['nf60']>=100 and r['nf5']>=25)
evalgate('nf60>=150 & nf5>=50', lambda r: r['nf60']>=150 and r['nf5']>=50)
evalgate('nf60>=100', lambda r: r['nf60']>=100)
evalgate('nf60>=150', lambda r: r['nf60']>=150)
evalgate('nf60>=200', lambda r: r['nf60']>=200)
evalgate('nf60>=100 & imb15>=0.3', lambda r: r['nf60']>=100 and (r['imb15'] or 0)>=0.3)
evalgate('nf60>=80 & nrb>=3', lambda r: r['nf60']>=80 and r['nrb']>=3)
