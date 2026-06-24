import json, statistics, pickle
pairs=pickle.load(open('_cd_pairs.pkl','rb'))
# inspect entry_meta structure of a buy: are chart_* top-level (raw) or under holder?
b=pairs[0][0]
m=b.get('entry_meta',{})
print('top-level entry_meta keys count', len(m))
for key in ['chart_trendline_5m_breakdown','chart_vp_above_poc','chart_sr_5m_below_broken','net_flow_15s_imbalance','bb_pos_15m','trend_30m_r_squared']:
    print(key, 'TOPLEVEL' if key in m else 'nested/absent', '=', m.get(key))
# Is there a holder_features / _hf block?
print('\nkeys containing holder or _hf:')
for k in m.keys():
    if 'holder' in k.lower() or k.endswith('_hf'): print(' ',k)
# value distribution of chart_trendline_5m_breakdown
vals=[bb.get('entry_meta',{}).get('chart_trendline_5m_breakdown') for bb,s,pp in pairs]
from collections import Counter
print('\nbreakdown value counts:', Counter(vals))
