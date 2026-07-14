import pickle
from collections import Counter
scr=pickle.load(open('scratchpad/_trips.pkl','rb'))
n=len(scr)
num=Counter()
for c in scr:
    for k,v in c['em'].items():
        if not isinstance(v,bool) and isinstance(v,(int,float)): num[k]+=1
want=['net_flow_5m_usd','net_flow_5m_imbalance','net_flow_60s_usd','net_flow_60s_imbalance','dip_volume_ratio','large_buyer_volume_pct','n_recurring_buyers_3plus','buy_size_mean_prior60s','buyer','pc_h6','turnover_h24_ratio','peak_h24_6h_pct','cnn_outcome_prob']
print('=== task-named / buyer features ===')
for k in sorted(num):
    if any(w in k for w in ['net_flow','dip_vol','large_buyer','recurring','buy_size','buyer','maker','imbalance','avg_trade','n_large']):
        print(f'  {k}: {num[k]}')
# categorical verdicts coverage: count PASS/BLOCK distribution
print('=== filter_*_verdict distributions (recent) ===')
verd=Counter()
for c in scr:
    for k,v in c['em'].items():
        if k.endswith('_verdict') and isinstance(v,str):
            verd[(k,v)]+=1
byf=Counter()
for (k,v),ct in verd.items(): byf[k]+=ct
for k in sorted(byf):
    dist={v:ct for (kk,v),ct in verd.items() if kk==k}
    blk=dist.get('BLOCK',0)
    if 30<=blk<=n-30:  # both classes present meaningfully
        print(f'  {k}: {dist}')
