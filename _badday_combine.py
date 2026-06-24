import json,statistics
from collections import Counter,defaultdict
recs=json.load(open('_badday_recs.json'))
days=['2026-06-21','2026-06-22','2026-06-23']
def getf(r,f):
    v=r['em'].get(f)
    return v if isinstance(v,(int,float)) and not isinstance(v,bool) else None

# token concentration check for a subset (which side, what addrs)
def concentration(group):
    c=Counter(r['addr'][:6] for r in group)
    top=c.most_common(3)
    n=len(group)
    return top, (top[0][1]/n if n else 0)

# The 3/3-consistent candidates with directions
cands=[
 ('net_flow_60s_imbalance',0.255,'>='),  # hi better
 ('btc_pc_h4',-0.0665,'>='),
 ('net_flow_60s_usd',46.24,'>='),
 ('unique_buyer_ratio',0.842,'>='),
 ('dip_volume_ratio',1.466,'<'),         # lo better
]
print("=== Single-feature PASS cohorts (the side we'd KEEP) ===")
for f,thr,op in cands:
    vals=[(getf(r,f),r) for r in recs]; vals=[(v,r) for v,r in vals if v is not None]
    if op=='>=': keep=[r for v,r in vals if v>=thr]; block=[r for v,r in vals if v<thr]
    else: keep=[r for v,r in vals if v<thr]; block=[r for v,r in vals if v>=thr]
    def st(g): return (len(g),sum(x['win'] for x in g)/len(g) if g else 0,statistics.mean(x['real'] for x in g) if g else 0)
    kn,kw,km=st(keep); bn,bw,bm=st(block)
    top,frac=concentration(keep)
    print(f"\n{f} {op}{thr}:")
    print(f"  KEEP n={kn} WR={kw:.2f} real={km:.2f} | BLOCK n={bn} WR={bw:.2f} real={bm:.2f}")
    print(f"  keep token-conc top3={top} maxfrac={frac:.2f}")
    pd=[]
    for dd in days:
        kk=[r for r in keep if r['day']==dd]; bb=[r for r in block if r['day']==dd]
        pd.append((dd,len(kk),round(statistics.mean(x['real'] for x in kk),1) if kk else None,
                      len(bb),round(statistics.mean(x['real'] for x in bb),1) if bb else None))
    print(f"  perday (day,keepN,keepReal,blockN,blockReal): {pd}")

# ---- Simple 2-of-N vote combiner (overfit-resistant): block if FAILS multiple decision-time gates ----
print("\n\n=== VOTE COMBINER: count of PASS gates ===")
def gates(r):
    g=[]
    v=getf(r,'net_flow_60s_imbalance'); g.append(v is not None and v>=0.255)
    v=getf(r,'unique_buyer_ratio');     g.append(v is not None and v>=0.842)
    v=getf(r,'btc_pc_h4');              g.append(v is not None and v>=-0.0665)
    v=getf(r,'dip_volume_ratio');       g.append(v is not None and v<1.466)
    return sum(1 for x in g if x), len(g)
buckets=defaultdict(list)
for r in recs:
    p,n=gates(r); buckets[p].append(r)
for p in sorted(buckets):
    g=buckets[p]
    print(f"  {p} gates pass: n={len(g)} WR={sum(x['win'] for x in g)/len(g):.2f} real={statistics.mean(x['real'] for x in g):.2f}")
# threshold: keep if >=3 of 4
keep=[r for r in recs if gates(r)[0]>=3]; block=[r for r in recs if gates(r)[0]<3]
print(f"\n  RULE keep>=3of4: KEEP n={len(keep)} WR={sum(x['win'] for x in keep)/len(keep):.2f} real={statistics.mean(x['real'] for x in keep):.2f}")
print(f"             BLOCK n={len(block)} WR={sum(x['win'] for x in block)/len(block):.2f} real={statistics.mean(x['real'] for x in block):.2f}")
for dd in days:
    kk=[r for r in keep if r['day']==dd]; bb=[r for r in block if r['day']==dd]
    print(f"    {dd}: keep n={len(kk)} real={statistics.mean(x['real'] for x in kk):.1f if kk else 0}" if kk else f"    {dd}: keep n=0",
          f"| block n={len(bb)} real={statistics.mean(x['real'] for x in bb):.1f}" if bb else "| block n=0")
top,frac=concentration(keep); print(f"  keep token-conc top3={top} maxfrac={frac:.2f}")
