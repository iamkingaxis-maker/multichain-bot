import json, math, statistics
from collections import Counter, defaultdict

recs = json.load(open('scratchpad/_dsg_recs.json'))
N = len(recs)

# ---------- helpers ----------
def auc(pos, neg):
    """Mann-Whitney AUC: P(score_pos > score_neg). pos,neg are lists of scores."""
    pos=[x for x in pos if x is not None]; neg=[x for x in neg if x is not None]
    if not pos or not neg: return float('nan'), 0,0
    # rank-based
    allv = sorted([(v,0) for v in neg]+[(v,1) for v in pos])
    # assign ranks with ties averaged
    ranks={}
    i=0
    vals=[v for v,_ in allv]
    n=len(vals)
    ranks_arr=[0.0]*n
    i=0
    while i<n:
        j=i
        while j<n and vals[j]==vals[i]:
            j+=1
        r=(i+1+j)/2.0
        for k in range(i,j): ranks_arr[k]=r
        i=j
    sum_pos=sum(ranks_arr[k] for k in range(n) if allv[k][1]==1)
    npos=len(pos); nneg=len(neg)
    U = sum_pos - npos*(npos+1)/2.0
    return U/(npos*nneg), npos, nneg

def logsafe(x):
    return math.log(max(x,1.0))

# ---------- winner/loser labels ----------
def split(rs):
    W=[r for r in rs if r['peak']>=20]
    L=[r for r in rs if r['peak']<6]
    return W,L

W,L = split(recs)
print(f'ALL: n={N} winners(peak>=20)={len(W)} losers(peak<6)={len(L)}')

# ---------- single-feature AUC (winners vs losers) ----------
print('\n=== Single-feature AUC (winner peak>=20 vs loser peak<6), higher=better ===')
feats = ['imb','nf5','accel','nf60','nrb','imb15','lbv']
for f in feats:
    a,np_,nn = auc([r[f] for r in W],[r[f] for r in L])
    print(f'  {f:6s} AUC={a:.3f}')

# ---------- build composite demand-shape score (FIXED weights, not fit) ----------
# Use robust rank-percentile of each positive-separating feature, equal weight.
# positive separators: imb, accel, nf5(log), nf60(log), nrb
def pct_rank_map(vals):
    s=sorted(vals)
    n=len(s)
    def f(x):
        # fraction of values <= x
        lo,hi=0,n
        # count <=x
        import bisect
        return bisect.bisect_right(s,x)/n
    return f
import bisect
def make_ranker(vals):
    s=sorted(v for v in vals if v is not None)
    def f(x):
        if x is None: return 0.5
        return bisect.bisect_right(s,x)/len(s)
    return s and f or (lambda x:0.5)

# transforms
for r in recs:
    r['lnf5']=logsafe(r['nf5'])
    r['lnf60']=logsafe(r['nf60'])
    r['accel_c']= r['accel'] if r['accel'] is not None else 0.0

comp_feats = ['imb','accel_c','lnf5','lnf60','nrb']
rankers={f:make_ranker([r[f] for r in recs]) for f in comp_feats}
for r in recs:
    r['score']=sum(rankers[f](r[f]) for f in comp_feats)/len(comp_feats)

W,L=split(recs)
a,_,_=auc([r['score'] for r in W],[r['score'] for r in L])
print(f'\n=== COMPOSITE demand-shape score (equal-weight rank of imb,accel,ln nf5,ln nf60,nrb) ===')
print(f'  overall AUC={a:.3f}')

# also a leaner score: imb + accel + lnf5 only
comp2=['imb','accel_c','lnf5']
rankers2={f:make_ranker([r[f] for r in recs]) for f in comp2}
for r in recs:
    r['score3']=sum(rankers2[f](r[f]) for f in comp2)/len(comp2)
W,L=split(recs)
a3,_,_=auc([r['score3'] for r in W],[r['score3'] for r in L])
print(f'  lean score3 (imb,accel,ln nf5) AUC={a3:.3f}')

# ---------- FOUR-HALF OOS: chrono x parity ----------
days=sorted(set(r['day'] for r in recs))
med_day=days[len(days)//2]
for r in recs:
    r['chrono']='early' if r['day']<med_day else 'late'
    r['parity']='odd' if r['day']%2==1 else 'even'
    r['quarter']=r['chrono']+'-'+r['parity']

print('\n=== FOUR-HALF OOS: AUC of composite score per quarter (chrono x parity) ===')
print('day range:', min(days),'->',max(days),' med_day=',med_day)
for q in ['early-odd','early-even','late-odd','late-even']:
    sub=[r for r in recs if r['quarter']==q]
    Wq,Lq=split(sub)
    a,np_,nn=auc([r['score'] for r in Wq],[r['score'] for r in Lq])
    a3,_,_=auc([r['score3'] for r in Wq],[r['score3'] for r in Lq])
    print(f'  {q:11s} n={len(sub):4d} W={np_:3d} L={nn:3d}  AUC_comp={a:.3f}  AUC_lean={a3:.3f}')

# also chrono halves and parity halves separately
print('\n  --- by chrono half ---')
for c in ['early','late']:
    sub=[r for r in recs if r['chrono']==c]
    Wq,Lq=split(sub)
    a,np_,nn=auc([r['score'] for r in Wq],[r['score'] for r in Lq])
    print(f'  {c:6s} n={len(sub)} W={np_} L={nn} AUC={a:.3f}')
print('  --- by parity half ---')
for p in ['odd','even']:
    sub=[r for r in recs if r['parity']==p]
    Wq,Lq=split(sub)
    a,np_,nn=auc([r['score'] for r in Wq],[r['score'] for r in Lq])
    print(f'  {p:6s} n={len(sub)} W={np_} L={nn} AUC={a:.3f}')

json.dump(recs, open('scratchpad/_dsg_recs2.json','w'))
