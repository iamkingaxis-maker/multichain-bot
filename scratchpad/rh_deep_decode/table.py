import json, statistics as st
from collections import defaultdict, Counter
BUYS=defaultdict(list); SELLS=defaultdict(list)
for l in open('scratchpad/robinhood_tapes/rh_paper_trades.jsonl'):
    d=json.loads(l); b=d.get('bot_id')
    if not b: continue
    if d['ev']=='buy': BUYS[b].append(d)
    elif d['ev']=='sell': SELLS[b].append(d)
def med(xs): return round(st.median(xs),1) if xs else None
LOSS={'PRE_STOP_BAIL','HARD_STOP','LP_DRAIN'}
def trips(bot):
    tr=[]; cur=[]
    for s in sorted(SELLS[bot],key=lambda x:x['ts']):
        cur.append(s)
        if s.get('fully'): tr.append(cur); cur=[]
    if cur: tr.append(cur)
    return tr
rows=[('GREEN','rh_deep_only'),('GREEN','rh_bites2'),('GREEN','rh_f_arc_scalp'),
      ('RED','rh_demand_heavy'),('RED','rh_wide_ladder'),('RED','rh_moonbag')]
print(f"{'grp':5} {'bot':16} {'dipMed':>6} {'nbuy':>4} {'ntrip':>5} {'lossLeg%':>8} {'tp2reach%':>9} {'moontail':>8} {'tripStd':>7}")
for g,bot in rows:
    dips=[b['dip_pct'] for b in BUYS[bot] if b.get('dip_pct') is not None]
    legs=[s for t in trips(bot) for s in t]
    nlegs=len(legs)
    if nlegs==0:
        print(f"{g:5} {bot:16} {'--':>6} {'0':>4} {'0':>5}   (no local fires; config+backtest only)")
        continue
    kc=Counter(s['kind'] for s in legs)
    lossleg=sum(kc[k] for k in LOSS)
    tp2=kc.get('TP2',0)
    moon=kc.get('MOONBAG_FLOOR',0)+kc.get('MOONBAG_TRAIL',0)
    tr=trips(bot); trpnl=[sum(s.get('pnl_usd',0)or 0 for s in t) for t in tr]
    trstd=round(st.pstdev(trpnl),2) if len(trpnl)>1 else 0
    print(f"{g:5} {bot:16} {med(dips)!s:>6} {len(BUYS[bot]):>4} {len(tr):>5} {100*lossleg/nlegs:>7.0f}% {100*tp2/nlegs:>8.0f}% {moon:>8} {trstd:>7}")
