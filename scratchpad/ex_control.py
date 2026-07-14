import json, statistics as st
from collections import defaultdict
E=json.load(open('_ex_episodes.json'))
def num(x):
    try:
        if x is None or isinstance(x,bool): return None
        return float(x)
    except: return None
def wr(rows): return (len(rows), (sum(r['win'] for r in rows)/len(rows) if rows else 0), (st.median([r['med_pnl'] for r in rows]) if rows else 0))
def absorb(r):
    v=num(r.get('largest_buy_to_largest_sell')); return v is not None and v>=1.0

# liquidity control: within liq terciles, does absorption still lift?
liqs=sorted([num(r.get('liquidity_usd')) for r in E if num(r.get('liquidity_usd')) is not None])
q1,q2=liqs[len(liqs)//3],liqs[2*len(liqs)//3]
def liqband(r):
    v=num(r.get('liquidity_usd'))
    if v is None: return 'na'
    return 'lo' if v<q1 else ('mid' if v<q2 else 'hi')
print('liq terciles q1=%.0f q2=%.0f'%(q1,q2))
for band in ['lo','mid','hi']:
    sub=[r for r in E if liqband(r)==band]
    ab=[r for r in sub if absorb(r)]; na=[r for r in sub if num(r.get('largest_buy_to_largest_sell')) is not None and not absorb(r)]
    print('  band %-3s absorb>=1: n=%3d WR=%.2f pnl%+5.1f | absorb<1: n=%3d WR=%.2f pnl%+5.1f'%(
        band,*wr(ab),*wr(na)))
print()
# pair robustness with threshold >=0.9 to widen n, and require >=3 each side
for thr,need in [(1.0,2),(0.9,2),(1.0,3)]:
    bypair=defaultdict(lambda:{'y':[],'n':[]})
    for r in E:
        v=num(r.get('largest_buy_to_largest_sell'))
        if v is None: continue
        bypair[r['addr']]['y' if v>=thr else 'n'].append(r['win'])
    rob=tot=ties=0
    for a,d in bypair.items():
        if len(d['y'])>=need and len(d['n'])>=need:
            tot+=1
            yy=sum(d['y'])/len(d['y']); nn=sum(d['n'])/len(d['n'])
            if yy>nn: rob+=1
            elif yy==nn: ties+=1
    print('absorption>=%.1f pair-rob (>=%d each side): %d win / %d tie / %d total'%(thr,need,rob,ties,tot))
print()
# base rate of absorption among all fills
n_ab=sum(1 for r in E if absorb(r)); n_has=sum(1 for r in E if num(r.get('largest_buy_to_largest_sell')) is not None)
print('absorption base rate: %d/%d = %.1f%% of fills that have the field'%(n_ab,n_has,100*n_ab/n_has))
print()
# Is absorption just proxying liquidity? median liq of absorb vs not
al=[num(r.get('liquidity_usd')) for r in E if absorb(r) and num(r.get('liquidity_usd')) is not None]
nl=[num(r.get('liquidity_usd')) for r in E if not absorb(r) and num(r.get('largest_buy_to_largest_sell')) is not None and num(r.get('liquidity_usd')) is not None]
print('median liq absorb=%.0f vs non=%.0f'%(st.median(al),st.median(nl)))
