import json, statistics as st
from collections import defaultdict
E=json.load(open('_ex_episodes.json'))
def num(x):
    try:
        if x is None or isinstance(x,bool): return None
        return float(x)
    except: return None
def summ(rows):
    if not rows: return '(empty)'
    wr=sum(r['win'] for r in rows)/len(rows)
    return 'n=%3d WR=%.2f medpnl=%+5.1f maxpnlMed=%+5.1f'%(len(rows),wr,st.median([r['med_pnl'] for r in rows]),st.median([r['max_pnl'] for r in rows]))

# young/1s-tracked cohort: has 1s_bars_since_low_60s and cascade fields
young=[r for r in E if num(r.get('1s_bars_since_low_60s')) is not None]
print('YOUNG/1s-tracked cohort',summ(young),'(of',len(E),')')
print()

# EXHAUSTION-PRESENT predicate: absorption (big buy>=big sell) AND a flush reversal signal
def exh_present(r):
    a=num(r.get('largest_buy_to_largest_sell'))
    casc=r.get('1s_cascade_reversal_detected') is True
    nf15=num(r.get('net_flow_15s_imbalance'))
    return (a is not None and a>=1.0) or casc

def demand_present(r):
    bb=num(r.get('buy_burst_30s_count'))
    bp=num(r.get('buy_pressure_60s'))
    return (bb is not None and bb>=1) or (bp is not None and bp>=0.7)

print('== EXHAUSTION-PRESENT (absorption big-buy>=big-sell OR cascade-reversal) ==')
ep=[r for r in E if exh_present(r)]; en=[r for r in E if not exh_present(r)]
print(' present ',summ(ep))
print(' absent  ',summ(en))
print()
print('== DEMAND-PRESENT (buy_burst>=1 OR buy_pressure>=0.7) ==')
dp=[r for r in E if demand_present(r)]; dn=[r for r in E if not demand_present(r)]
print(' present ',summ(dp))
print(' absent  ',summ(dn))
print()
print('== ABSORPTION only (largest_buy_to_largest_sell>=1.0: a buy at least as big as biggest sell) ==')
ab=[r for r in E if (num(r.get('largest_buy_to_largest_sell')) or 0)>=1.0]
na=[r for r in E if (num(r.get('largest_buy_to_largest_sell')) or -1)<1.0 and num(r.get('largest_buy_to_largest_sell')) is not None]
print(' absorb>=1 ',summ(ab))
print(' absorb<1  ',summ(na))
# per-pair robustness of absorption
bypair=defaultdict(lambda:{'y':[],'n':[]})
for r in E:
    v=num(r.get('largest_buy_to_largest_sell'))
    if v is None: continue
    bypair[r['addr']]['y' if v>=1.0 else 'n'].append(r['win'])
rob=tot=0
for a,d in bypair.items():
    if len(d['y'])>=2 and len(d['n'])>=2:
        tot+=1
        if sum(d['y'])/len(d['y'])>sum(d['n'])/len(d['n']): rob+=1
print(' absorption pair-robustness %d/%d'%(rob,tot))
print()
print('== COMBINED: absorption>=1 AND net_flow_15s_imbalance>0 (buyers turning positive short-term) ==')
comb=[r for r in E if (num(r.get('largest_buy_to_largest_sell')) or 0)>=1.0 and (num(r.get('net_flow_15s_imbalance')) or -9)>0]
print(' ',summ(comb))
print()
# does absorption help WITHIN the young flush cohort specifically?
print('== within YOUNG cohort ==')
yab=[r for r in young if (num(r.get('largest_buy_to_largest_sell')) or 0)>=1.0]
yna=[r for r in young if num(r.get('largest_buy_to_largest_sell')) is not None and (num(r.get('largest_buy_to_largest_sell')))<1.0]
print(' absorb>=1',summ(yab))
print(' absorb<1 ',summ(yna))
ycr=[r for r in young if r.get('1s_cascade_reversal_detected') is True]
print(' cascade_reversal',summ(ycr))
