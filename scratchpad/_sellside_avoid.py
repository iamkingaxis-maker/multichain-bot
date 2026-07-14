import json
from collections import defaultdict
D=json.load(open(r'C:\Users\jcole\multichain-bot\scratchpad\_sellside_metrics.json'))
eps=1e-9
DD=[o for o in D if o['label'] in('CONT','TOP')]
base=sum(o['label']=='CONT' for o in DD)/len(DD)
print(f"base CONT={100*base:.1f}%  N={len(DD)}\n")
def svr(o): return o['m60']['sv']/60.0
def traj(o):
    m=o['m60']; return m['sv_late']/(m['sv_early']+eps)
def maxs(o): return o['m60']['max_sell']

# find sell-rate cut that best flags TOP
svs=sorted(svr(o) for o in DD)
for q in [0.6,0.667,0.7,0.75,0.8,0.85,0.9]:
    cut=svs[int(q*len(svs))]
    heavy=[o for o in DD if svr(o)>=cut]
    if not heavy: continue
    c=sum(o['label']=='CONT' for o in heavy)
    print(f"sell$/s>={cut:7.2f} (q{q:.2f}): CONT={c}/{len(heavy)}={100*c/len(heavy):.1f}%  toks={len(set(o['tid'] for o in heavy))}")

print("\n--- DISTRIBUTION rule: heavy sells (q>=0.75) AND accelerating (traj>=1) ---")
cut=svs[int(0.75*len(svs))]
dist=[o for o in DD if svr(o)>=cut and traj(o)>=1.0]
notd=[o for o in DD if not(svr(o)>=cut and traj(o)>=1.0)]
def cr(x):
    c=sum(o['label']=='CONT' for o in x); return f"CONT={c}/{len(x)}={100*c/len(x):.1f}%" if x else "empty"
print(f"  DISTRIBUTION (avoid): {cr(dist)}  toks={len(set(o['tid'] for o in dist))}")
print(f"  rest (tradeable)    : {cr(notd)}  toks={len(set(o['tid'] for o in notd))}")

# per-token robustness of DISTRIBUTION flag: among tokens with a flagged & unflagged episode,
# is TOP-rate higher in flagged?
by=defaultdict(lambda:{'f':[], 'u':[]})
for o in DD:
    flag = svr(o)>=cut and traj(o)>=1.0
    by[o['tid']]['f' if flag else 'u'].append(o['label'])
agree=tot=0
for t,d in by.items():
    if d['f'] and d['u']:
        tot+=1
        tf=sum(l=='TOP' for l in d['f'])/len(d['f'])
        tu=sum(l=='TOP' for l in d['u'])/len(d['u'])
        if tf>tu: agree+=1
        elif tf==tu: tot-=1
print(f"  per-token: flagged has higher TOP-rate in {agree}/{tot} tokens ({100*agree/tot:.0f}%)")
