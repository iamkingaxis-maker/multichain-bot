import json, statistics, math
rows=json.load(open('_vf_rows.json'))
def scrub(rs): return [r for r in rs if not((r['pnl'] or 0)>0 and (r['hold'] or 0)<10)]
def wr(rs): 
    n=len(rs); w=sum(1 for r in rs if (r['pnl'] or 0)>0); return (100*w/n if n else 0), n, w
def med(rs): 
    v=[r['pnl'] for r in rs if r['pnl'] is not None]; return statistics.median(v) if v else None
def npairs(rs): return len(set(r['pair'] for r in rs))
def two_prop_z(rs1,rs2):
    w1=sum(1 for r in rs1 if (r['pnl'] or 0)>0); n1=len(rs1)
    w2=sum(1 for r in rs2 if (r['pnl'] or 0)>0); n2=len(rs2)
    p=(w1+w2)/(n1+n2); se=math.sqrt(p*(1-p)*(1/n1+1/n2))
    return (w1/n1-w2/n2)/se if se>0 else 0

allr=[r for r in rows if r['bsl'] is not None]
s=scrub(allr)
print('=== bsl known, scrubbed:',len(s),'(raw',len(allr),') ===')
knife=[r for r in s if r['bsl']<10]
conf=[r for r in s if 10<=r['bsl']<=35]
mid=[r for r in s if 10<=r['bsl']<50]
hi=[r for r in s if r['bsl']>=50]
for name,rs in [('bsl<10 (knife)',knife),('bsl 10-35 (confirm)',conf),('bsl 10-49',mid),('bsl>=50',hi)]:
    w,n,ww=wr(rs); print(f'{name:22s} WR={w:5.1f}% n={n:4d} pairs={npairs(rs):3d} medpnl={med(rs):+.2f}')
print('two-prop z (conf vs knife):',round(two_prop_z(conf,knife),2))

print()
print('=== demand gate: ub>=10 AND nf15>0 ===')
def gate(r): return (r['ub'] or 0)>=10 and (r['nf15'] or 0)>0
gpass=[r for r in s if gate(r)]
gpass_conf=[r for r in gpass if 10<=r['bsl']<=35]
gpass_knife=[r for r in gpass if r['bsl']<10]
for name,rs in [('gate all',gpass),('gate+bsl10-35',gpass_conf),('gate+bsl<10',gpass_knife)]:
    w,n,ww=wr(rs); m=med(rs)
    print(f'{name:18s} WR={w:5.1f}% n={n:4d} pairs={npairs(rs):3d} medpnl={m:+.2f}' if n else f'{name} empty')
print('two-prop z (gate+conf vs gate all):',round(two_prop_z(gpass_conf,gpass),2))

print()
print('=== hl_confirm_state distribution (bsl-known) ===')
from collections import Counter
print(Counter(r['hl'] for r in allr))

print()
print('=== LIVE only (real money) ===')
live=[r for r in s if r['live']]
lk=[r for r in live if r['bsl']<10]; lc=[r for r in live if 10<=r['bsl']<=35]
for name,rs in [('live bsl<10',lk),('live bsl10-35',lc)]:
    w,n,ww=wr(rs); print(f'{name:16s} WR={w:5.1f}% n={n:4d} pairs={npairs(rs):3d} medpnl={med(rs) if n else None}')
