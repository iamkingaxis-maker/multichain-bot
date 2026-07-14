import json, math, bisect
from collections import Counter

recs = json.load(open('scratchpad/_dsg_recs2.json'))
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
    npos=len(pos); nneg=len(neg)
    U=sp-npos*(npos+1)/2.0
    return U/(npos*nneg)

def split(rs):
    return [r for r in rs if r['peak']>=20],[r for r in rs if r['peak']<6]

# ---- 4 chronological quarters (equal count) ----
q=N//4
quarters=[recs[0:q],recs[q:2*q],recs[2*q:3*q],recs[3*q:]]
from datetime import datetime,timezone
def lbl(rs):
    a=datetime.fromtimestamp(rs[0]['ts'],tz=timezone.utc).strftime('%m-%d %Hh')
    b=datetime.fromtimestamp(rs[-1]['ts'],tz=timezone.utc).strftime('%m-%d %Hh')
    return a+'->'+b

print('=== 4 CHRONOLOGICAL QUARTERS: AUC of separators (winner peak>=20 vs loser peak<6) ===')
print(f'{"quarter":24s} {"n":>4s} {"W":>3s} {"L":>3s} {"accel":>6s} {"nf60":>6s} {"imb":>6s} {"score":>6s}')
for i,qr in enumerate(quarters):
    Wq,Lq=split(qr)
    row=[auc([r[f] for r in Wq],[r[f] for r in Lq]) for f in ['accel_c','nf60','imb','score']]
    print(f'Q{i+1} {lbl(qr):20s} {len(qr):4d} {len(Wq):3d} {len(Lq):3d} '+' '.join(f'{x:6.3f}' for x in row))

# overall
Wa,La=split(recs)
print(f'\nOVERALL accel AUC={auc([r["accel_c"] for r in Wa],[r["accel_c"] for r in La]):.3f}  '
      f'nf60={auc([r["nf60"] for r in Wa],[r["nf60"] for r in La]):.3f}  '
      f'imb={auc([r["imb"] for r in Wa],[r["imb"] for r in La]):.3f}  '
      f'score={auc([r["score"] for r in Wa],[r["score"] for r in La]):.3f}')

# ================= GATE TEST =================
# Gate skips the "loser signature": low score (low imbalance / decaying flow).
# Sweep score percentile thresholds; keep entries with score >= threshold.
POS=25.0  # base_position_usd for net-$ accounting
def metrics(rs):
    n=len(rs)
    if n==0: return dict(n=0,wr=float('nan'),mean=float('nan'),net=0.0,extop2=float('nan'))
    wr=sum(1 for r in rs if r['pnl_pct']>0)/n
    mean=sum(r['pnl_pct'] for r in rs)/n
    net=sum(r['pnl_pct']/100.0*POS for r in rs)
    # ex-top-2: drop 2 highest pnl_pct, take median of rest
    srt=sorted(r['pnl_pct'] for r in rs)
    ex=srt[:-2] if n>2 else []
    extop2=(ex[len(ex)//2] if ex else float('nan'))
    return dict(n=n,wr=wr,mean=mean,net=net,extop2=extop2,
                nwin=sum(1 for r in rs if r['peak']>=20))

base=metrics(recs)
print('\n=== GATE TEST: keep entries with demand-shape score >= threshold ===')
print(f'BASELINE (no gate): n={base["n"]} wr={base["wr"]*100:.1f}% mean_ret={base["mean"]:.2f}% '
      f'net@$25=${base["net"]:.2f} ex-top2_med={base["extop2"]:.3f}% winners={base["nwin"]}')

scores=sorted(r['score'] for r in recs)
print(f'\n{"thresh_pct":>10s} {"keep_n":>7s} {"thru%":>6s} {"wr%":>6s} {"mean%":>7s} {"net$":>8s} '
      f'{"net$/pos":>8s} {"win_kept":>8s} {"win_skip":>8s} {"los_avoid":>9s} {"extop2%":>8s}')
for pctile in [0,10,20,30,40,50,60,70]:
    thr=scores[int(len(scores)*pctile/100)] if pctile>0 else -1
    kept=[r for r in recs if r['score']>=thr]
    skip=[r for r in recs if r['score']<thr]
    m=metrics(kept)
    win_skip=sum(1 for r in skip if r['peak']>=20)
    los_avoid=sum(1 for r in skip if r['peak']<6)
    netpos=m['net']/m['n'] if m['n'] else 0
    print(f'{pctile:9d}% {m["n"]:7d} {m["n"]/N*100:5.1f}% {m["wr"]*100:5.1f}% {m["mean"]:6.2f}% '
          f'{m["net"]:7.2f} {netpos:8.3f} {m["nwin"]:8d} {win_skip:8d} {los_avoid:9d} {m["extop2"]:7.3f}%')
