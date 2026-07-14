import json, statistics as st
rows=json.load(open('_pos_rows.json'))
def stat(sub,label):
    if not sub: print(f'{label:42s} n=0'); return
    p=[r['rpnl'] for r in sub]; dt=len(set(r['address'] for r in sub))
    ev=sum(p)/len(p); wr=100*sum(1 for x in p if x>0)/len(p)
    print(f'{label:42s} n={len(sub):4d} dt={dt:3d} EV={ev:7.2f} med={st.median(p):7.2f} wr={wr:4.1f}')

print('=== DEMAND: median_buy_size_usd ===')
for lo,hi in [(0,5),(5,10),(10,20),(20,40),(40,1e9)]:
    stat([r for r in rows if r['med_buy'] is not None and lo<=r['med_buy']<hi],f'med_buy {lo}-{hi}')
print('=== DEMAND: unique_buyers_n ===')
for lo,hi in [(0,15),(15,30),(30,50),(50,1e9)]:
    stat([r for r in rows if r['uniq'] is not None and lo<=r['uniq']<hi],f'uniq {lo}-{hi}')
print('=== DEMAND: buy_size_mean_trend (accel) ===')
for lo,hi in [(-1e9,0.8),(0.8,1.0),(1.0,1.3),(1.3,1e9)]:
    stat([r for r in rows if r['buy_trend'] is not None and lo<=r['buy_trend']<hi],f'buytrend {lo}-{hi}')
print('=== VOLATILITY (token_volatility_h24_pct) ===')
for lo,hi in [(0,80),(80,150),(150,300),(300,1e9)]:
    stat([r for r in rows if r['vol'] is not None and lo<=r['vol']<hi],f'vol {lo}-{hi}')
print('=== rsi_15m ===')
for lo,hi in [(0,40),(40,55),(55,70),(70,1e9)]:
    stat([r for r in rows if r['rsi15'] is not None and lo<=r['rsi15']<hi],f'rsi15 {lo}-{hi}')
