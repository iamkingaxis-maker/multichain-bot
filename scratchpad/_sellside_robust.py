import json
from collections import defaultdict
import statistics as st

D=json.load(open(r'C:\Users\jcole\multichain-bot\scratchpad\_sellside_metrics.json'))
eps=1e-9

def feats(o):
    m60=o['m60']; mleg=o['mleg']; lw=max(o['leg_win'],1)
    return dict(
      sellvol_rate60=m60['sv']/60.0,
      sellcnt_rate60=m60['sc']/60.0,
      sellvol_rate_leg=mleg['sv']/lw,
      sellcnt_rate_leg=mleg['sc']/lw,
      maxsell60=m60['max_sell'],
      sellshare60=m60['sv']/(m60['sv']+m60['bv']+eps),
      sellshare_leg=mleg['sv']/(mleg['sv']+mleg['bv']+eps),
      sell_traj60=m60['sv_late']/(m60['sv_early']+eps),
      bsr60=m60['bv']/(m60['sv']+eps),
      maxsell_traj60=m60['ms_late']/(m60['ms_early']+eps),
    )

# thesis direction: for these, TOP should be HIGHER (distribution). bsr60 CONT higher.
top_higher=['sellvol_rate60','sellcnt_rate60','sellvol_rate_leg','sellcnt_rate_leg',
            'maxsell60','sellshare60','sellshare_leg','sell_traj60','maxsell_traj60']
cont_higher=['bsr60']

by_tok=defaultdict(lambda: {'CONT':[],'TOP':[]})
for o in D:
    if o['label'] in ('CONT','TOP'):
        by_tok[o['tid']][o['label']].append(feats(o))

paired=[t for t,d in by_tok.items() if d['CONT'] and d['TOP']]
print('tokens with both CONT and TOP:',len(paired))

allf=top_higher+cont_higher
print(f"\n{'feature':20s} {'agree%':>7s} {'nTok':>5s}  (thesis-direction per-token)")
for f in allf:
    agree=0; nz=0
    for t in paired:
        d=by_tok[t]
        cv=st.median([x[f] for x in d['CONT']]); tv=st.median([x[f] for x in d['TOP']])
        if cv==tv: continue
        nz+=1
        want_top = f in top_higher
        if (tv>cv)==want_top: agree+=1
    print(f"{f:20s} {100*agree/nz:6.1f}% {nz:5d}")
