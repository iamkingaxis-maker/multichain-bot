import json, statistics
from collections import defaultdict
rows=json.load(open('_vf_rows.json'))
def scrub(rs): return [r for r in rs if not((r['pnl'] or 0)>0 and (r['hold'] or 0)<10)]
s=scrub([r for r in rows if r['bsl'] is not None])
def gate(r): return (r['ub'] or 0)>=10 and (r['nf15'] or 0)>0

# --- per-pair robustness for gate+bsl10-35 ---
cell=[r for r in s if gate(r) and 10<=r['bsl']<=35]
byp=defaultdict(list)
for r in cell: byp[r['pair']].append(r['pnl'])
win_pairs=sum(1 for p,v in byp.items() if statistics.median(v)>0)
pos_mean_pairs=sum(1 for p,v in byp.items() if statistics.mean(v)>0)
print(f'gate+bsl10-35: n={len(cell)} pairs={len(byp)} winning(medped>0)={win_pairs} mean>0 pairs={pos_mean_pairs}')
# distribution of per-pair median
pm=sorted(statistics.median(v) for v in byp.values())
print('per-pair median pnl quartiles:', [round(x,1) for x in (pm[0],pm[len(pm)//4],pm[len(pm)//2],pm[3*len(pm)//4],pm[-1])])
# top contributors — is it a few pairs?
tot=sum(sum(v) for v in byp.values())
contrib=sorted(((sum(v),p,len(v)) for p,v in byp.items()),reverse=True)
print('total sum pnl:',round(tot,1))
print('top 3 pairs sum:',[(round(c,1),n) for c,p,n in contrib[:3]])
print('bot spread:',len(set(r['bot'] for r in cell)),'bots')

# --- OUT OF SAMPLE: time split ---
print()
print('=== TIME SPLIT (median time) ===')
times=sorted(r['time'] for r in s)
mid=times[len(times)//2]
print('split at',mid)
def block(rs,early): return [r for r in rs if (r['time']<mid)==early]
def wr(rs): n=len(rs); return (100*sum(1 for r in rs if (r['pnl'] or 0)>0)/n if n else 0),n
for half,lbl in [(True,'EARLY'),(False,'LATE ')]:
    hs=block(s,half)
    knife=[r for r in hs if r['bsl']<10]
    conf=[r for r in hs if 10<=r['bsl']<=35]
    gconf=[r for r in hs if gate(r) and 10<=r['bsl']<=35]
    gall=[r for r in hs if gate(r)]
    wk,nk=wr(knife); wc,nc=wr(conf); wgc,ngc=wr(gconf); wga,nga=wr(gall)
    mg=statistics.median([r['pnl'] for r in gconf]) if gconf else None
    print(f'{lbl} knife {wk:.0f}%/{nk}  conf {wc:.0f}%/{nc}  | gate_all {wga:.0f}%/{nga}  gate+conf {wgc:.0f}%/{ngc} med={mg}')
