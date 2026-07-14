import pickle, statistics as st, random
from collections import defaultdict
scr=pickle.load(open('scratchpad/_trips.pkl','rb'))
random.seed(42)
def parity(c): return int(c['t'][8:10])%2
odd=sorted([c for c in scr if parity(c)==1],key=lambda c:c['t'])
even=sorted([c for c in scr if parity(c)==0],key=lambda c:c['t'])
def halves(r): m=len(r)//2; return r[:m],r[m:]
o1,o2=halves(odd); e1,e2=halves(even)
QUARTERS=[o1,o2,e1,e2]
def extop2(rows):
    by=defaultdict(list)
    for c in rows: by[c['tok']].append(c['ret'])
    tm=sorted(st.median(v) for v in by.values())
    if len(tm)<4: return None
    return st.median(tm[:-2])
bases=[extop2(q) for q in QUARTERS]
print('per-quarter BASELINE ex-top2:',[f'{b:.2f}' for b in bases])
# null: random gate at throughput p
for p in [0.3,0.5,0.7]:
    passes=0; N=2000
    for _ in range(N):
        npos=0
        for q,base in zip(QUARTERS,bases):
            kept=[c for c in q if random.random()<p]
            g=extop2(kept)
            if g is not None and base is not None and g>base: npos+=1
        if npos>=3: passes+=1
    print(f'null throughput={p}: P(>=3/4 positive lift)={passes/N:.3f}')
# in-sample full gated ex-top2 for top candidates vs -6.42 floor
def num(c,k):
    v=c['em'].get(k); return v if isinstance(v,(int,float)) and not isinstance(v,bool) else None
def verd(c,k):
    v=c['em'].get(k); return v if isinstance(v,str) else None
full_base=extop2(scr)
print(f'\nFULL-sample baseline ex-top2: {full_base:.2f}')
cands={
 'peak_h24_6h_pct<=100':lambda c:(num(c,'peak_h24_6h_pct') is not None and num(c,'peak_h24_6h_pct')<=100),
 'PASS:bs_m5_low':lambda c:verd(c,'filter_bs_m5_low_verdict')=='PASS',
 'trade_density>=1.2':lambda c:(num(c,'trade_density_30s_vs_5m') or 0)>=1.2,
 'buy_sell_imb>=0.55':lambda c:(num(c,'buy_sell_volume_imbalance') or -9)>=0.55,
 'prior_pch6le0_AND_buyer34':lambda c:(num(c,'pc_h6') is not None and num(c,'pc_h6')<=0) and (num(c,'mean_buy_size_usd') or 0)>=34,
}
for n,pr in cands.items():
    kept=[c for c in scr if pr(c)]
    g=extop2(kept)
    grn=100.0*sum(1 for k in set(c['tok'] for c in kept) )  # placeholder
    print(f'  {n:30s} full ex-top2={g:.2f} (base {full_base:.2f}, lift {g-full_base:+.2f}) thru={len(kept)/len(scr)*100:.0f}%')
