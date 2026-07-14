import json
from collections import defaultdict
D=json.load(open(r'C:\Users\jcole\multichain-bot\scratchpad\_sellside_metrics.json'))
DD=[o for o in D if o['label'] in('CONT','TOP')]
# distinct sell makers summed across episode windows (union not possible cross-token; report per-episode dsm)
tot_dsm60=sum(o['m60']['dsm'] for o in DD)
tot_sells60=sum(o['m60']['sc'] for o in DD)
print('episodes',len(DD),'tokens',len(set(o['tid'] for o in DD)))
print('sum distinct-sell-makers in 60s windows:',tot_dsm60,' sum sell-trades:',tot_sells60)
# largest-sell shrinking check: ms_late vs ms_early sign by label
import statistics as st
for lab in ('CONT','TOP'):
    sub=[o for o in DD if o['label']==lab]
    shrink=sum(1 for o in sub if o['m60']['ms_late']<o['m60']['ms_early'])
    grow=sum(1 for o in sub if o['m60']['ms_late']>o['m60']['ms_early'])
    print(f"{lab}: largest-sell shrink(late<early)={shrink} grow={grow} ratio_shrink={100*shrink/(shrink+grow+1e-9):.1f}%")
