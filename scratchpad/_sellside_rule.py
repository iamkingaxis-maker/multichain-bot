import json
from collections import defaultdict
import statistics as st

D=json.load(open(r'C:\Users\jcole\multichain-bot\scratchpad\_sellside_metrics.json'))
eps=1e-9
def sv_rate60(o): return o['m60']['sv']/60.0
def traj60(o):
    m=o['m60']; return m['sv_late']/(m['sv_early']+eps)
base_cont=sum(1 for o in D if o['label']=='CONT')
N=sum(1 for o in D if o['label'] in('CONT','TOP'))
print(f"BASE RATE CONT = {base_cont}/{N} = {100*base_cont/N:.1f}%\n")

# Tercile analysis of each signal (lower sell pressure -> more CONT?)
def terciles(fn,name):
    vals=sorted((fn(o),o['label']) for o in D if o['label'] in('CONT','TOP'))
    n=len(vals); t=n//3
    for lab,seg in [('LOW ',vals[:t]),('MID ',vals[t:2*t]),('HIGH',vals[2*t:])]:
        c=sum(1 for _,l in seg if l=='CONT'); m=len(seg)
        lo=seg[0][0]; hi=seg[-1][0]
        print(f"  {name} {lab} [{lo:8.3f},{hi:8.3f}] CONT={c}/{m}={100*c/m:.1f}%")
    print()
print("--- sell $/sec last 60s (LOW = sells dried up) ---")
terciles(sv_rate60,'svrate')
print("--- sell trajectory late/early last 60s (LOW = drying up) ---")
terciles(traj60,'traj')

# Combined rule: LOW sell-rate AND drying trajectory
svs=sorted(sv_rate60(o) for o in D if o['label'] in('CONT','TOP'))
med_sv=svs[len(svs)//2]
def rule_dry(o): return sv_rate60(o)<med_sv and traj60(o)<1.0  # sells below median AND decelerating
dry=[o for o in D if o['label'] in('CONT','TOP') and rule_dry(o)]
wet=[o for o in D if o['label'] in('CONT','TOP') and not rule_dry(o)]
def cr(x): 
    c=sum(1 for o in x if o['label']=='CONT'); return c,len(x),100*c/len(x) if x else 0
print("COMBINED RULE: sell$/sec<median AND sell-traj<1 (sells drying up)")
print(f"  DRY  (buy signal): CONT={cr(dry)}   tokens={len(set(o['tid'] for o in dry))}")
print(f"  WET  (avoid)     : CONT={cr(wet)}   tokens={len(set(o['tid'] for o in wet))}")

# whale check: is DRY-group edge concentrated? per-token CONT rate in dry vs overall
tok_dry=defaultdict(lambda:[0,0])
for o in dry: tok_dry[o['tid']][0]+= (o['label']=='CONT'); tok_dry[o['tid']][1]+=1
tokens_pos=sum(1 for t,(c,n) in tok_dry.items() if n>=2 and c/n>base_cont/N)
tokens_multi=sum(1 for t,(c,n) in tok_dry.items() if n>=2)
print(f"  DRY per-token(>=2 eps): {tokens_pos}/{tokens_multi} tokens beat base CONT-rate")
