import json
from collections import defaultdict
import statistics as st

D=json.load(open(r'C:\Users\jcole\multichain-bot\scratchpad\_sellside_metrics.json'))

def auc(vals_pos, vals_neg):
    # prob(pos > neg); pos=CONT here. AUC>0.5 => higher value assoc with CONT
    n=0; s=0
    for a in vals_pos:
        for b in vals_neg:
            n+=1
            if a>b: s+=1
            elif a==b: s+=0.5
    return s/n if n else float('nan')

def feat(o, name):
    m60=o['m60']; m120=o['m120']; mleg=o['mleg']
    eps=1e-9
    if name=='bsr60': return m60['bv']/(m60['sv']+eps)
    if name=='bsr_leg': return mleg['bv']/(mleg['sv']+eps)
    if name=='sellvol60': return m60['sv']
    if name=='sellcount60': return m60['sc']
    if name=='maxsell60': return m60['max_sell']
    if name=='maxsell_rel_buy60': return m60['max_sell']/(m60['bv']+eps)
    if name=='maxsell_rel_tot60': return m60['max_sell']/(m60['sv']+m60['bv']+eps)
    if name=='sellshare60': return m60['sv']/(m60['sv']+m60['bv']+eps)   # sell fraction of vol
    if name=='sell_traj60': return m60['sv_late']/(m60['sv_early']+eps)  # <1 = drying up
    if name=='maxsell_traj60': return m60['ms_late']/(m60['ms_early']+eps)
    if name=='dsm60': return m60['dsm']
    if name=='sellshare_leg': return mleg['sv']/(mleg['sv']+mleg['bv']+eps)
    if name=='maxsell_rel_buy_leg': return mleg['max_sell']/(mleg['bv']+eps)
    if name=='sell_traj_leg': return mleg['sv_late']/(mleg['sv_early']+eps)
    if name=='sellcount_leg': return mleg['sc']
    return None

feats=['bsr60','bsr_leg','sellvol60','sellcount60','maxsell60','maxsell_rel_buy60',
       'maxsell_rel_tot60','sellshare60','sell_traj60','maxsell_traj60','dsm60',
       'sellshare_leg','maxsell_rel_buy_leg','sell_traj_leg','sellcount_leg']

cont=[o for o in D if o['label']=='CONT']
top =[o for o in D if o['label']=='TOP']

print(f"{'feature':22s} {'AUC':>6s} {'CONT_med':>10s} {'TOP_med':>10s} {'dir'}")
res={}
for f in feats:
    cv=[feat(o,f) for o in cont]; tv=[feat(o,f) for o in top]
    # clip infinities
    cv=[min(v,1e6) for v in cv]; tv=[min(v,1e6) for v in tv]
    a=auc(cv,tv)
    res[f]=a
    dirn='CONT>TOP' if a>0.5 else 'TOP>CONT'
    print(f"{f:22s} {a:6.3f} {st.median(cv):10.3f} {st.median(tv):10.3f} {dirn}")
