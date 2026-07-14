import json, statistics as st
from datetime import datetime
from collections import defaultdict

t=json.load(open('_trades_fresh.json'))
def pt(s):
    return datetime.fromisoformat(s).timestamp()

by_addr=defaultdict(list)
for r in t:
    a=r.get('address')
    if a: by_addr[a].append(r)

EXHAUST=['sell_volume_decay_ratio_30s','1s_vol_decay_120s','1s_cascade_reversal_detected','1s_cascade_reversal_pct',
 '1s_cascade_reversal_close_pos','1s_sweep_reject_detected','1s_bottom_score','1s_vol_burst_on_reversal_ratio',
 '1s_bars_since_low_60s','1s_lower_wick_ratio_last','1s_close_pos_60s','1s_red_count_60s','1s_cascade_length',
 'net_flow_15s_usd','net_flow_60s_usd','net_flow_5m_usd','net_flow_15s_imbalance','net_flow_60s_imbalance','net_flow_5m_imbalance',
 'rt_max_sell_usd','rt_consec_sells','sell_burst_30s_count','largest_buy_to_largest_sell','buy_sell_volume_imbalance',
 'buy_pressure_60s','buy_burst_30s_count','hl_confirm_state','pc_h1','pc_h6','pc_h24','median_buy_size_usd','unique_buyers_n',
 'liquidity_usd','1s_base_confirmed_at_entry','1s_bars_60s']

episodes=[]
for a,rows in by_addr.items():
    rows=sorted(rows,key=lambda r: pt(r['time']))
    # build buy bursts
    bursts=[]
    cur=None
    for r in rows:
        if r.get('type')=='buy' and isinstance(r.get('entry_meta'),dict):
            tm=pt(r['time'])
            if cur and tm-cur['t0']<=6:
                cur['buys'].append(r)
            else:
                cur={'t0':tm,'buys':[r]}
                bursts.append(cur)
    # for each burst, exit sells = sells after burst t0 and before next burst t0
    for i,b in enumerate(bursts):
        t_start=pt(b['buys'][-1]['time'])  # last buy of burst
        t_next=bursts[i+1]['t0'] if i+1<len(bursts) else 1e18
        sells=[r for r in rows if r.get('type')=='sell' and r.get('pnl_pct') is not None
               and t_start-2<=pt(r['time'])<t_next]
        if not sells: continue
        pnls=[r['pnl_pct'] for r in sells]
        holds=[r.get('hold_secs') for r in sells if r.get('hold_secs') is not None]
        med_pnl=st.median(pnls)
        em=b['buys'][0]['entry_meta']
        ep={'addr':a,'sym':b['buys'][0].get('token'),'time':b['buys'][0]['time'],'t0':b['t0'],
            'reentry_idx':i,'n_sells':len(sells),'med_pnl':med_pnl,'max_pnl':max(pnls),
            'med_hold':(st.median(holds) if holds else None),'win':1 if med_pnl>0 else 0}
        for k in EXHAUST:
            ep[k]=em.get(k)
        episodes.append(ep)

json.dump(episodes,open('_ex_episodes.json','w'))
print('episodes',len(episodes))
wr=sum(e['win'] for e in episodes)/len(episodes)
print('base WR %.3f'%wr,'median pnl %.2f'%st.median([e['med_pnl'] for e in episodes]))
# reentry distribution
from collections import Counter
print('reentry idx dist',Counter(min(e['reentry_idx'],3) for e in episodes))
# DONALD
don=[e for e in episodes if 'J9fVUSrs' in e['addr']]
print('--- DONALD episodes',len(don))
for e in sorted(don,key=lambda x:x['t0']):
    print(e['time'][:19],'reidx',e['reentry_idx'],'medpnl %.1f'%e['med_pnl'],'win',e['win'],
          'sweep_rej',e['1s_sweep_reject_detected'],'casc_rev',e['1s_cascade_reversal_detected'],
          'selldecay',e['sell_volume_decay_ratio_30s'],'bars_since_low',e['1s_bars_since_low_60s'],
          'nf15',e['net_flow_15s_usd'],'nf60',e['net_flow_60s_usd'],'nf5m',e['net_flow_5m_usd'],
          'botscore',e['1s_bottom_score'],'buypress',e['buy_pressure_60s'])
