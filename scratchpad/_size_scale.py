import json, statistics
from collections import defaultdict

d = json.load(open('_ev_trades.json'))

# ---- reconstruct positions: group by (bot_id,address,entry_price) ----
buys = defaultdict(list); sells = defaultdict(list)
for x in d:
    key = (x.get('bot_id'), x.get('address'), x.get('entry_price'))
    if x.get('type')=='buy': buys[key].append(x)
    elif x.get('type')=='sell': sells[key].append(x)

positions=[]
for key, blist in buys.items():
    slist = sells.get(key)
    if not slist: continue
    b = blist[0]
    L = (b.get('entry_meta') or {}).get('liquidity_usd')
    if not L or L<=0: continue
    num=0.0; den=0.0
    for s in slist:
        f = s.get('sell_fraction') or 0
        p = s.get('pnl_pct')
        if p is None: continue
        num += f*p; den += f
    if den<=0: continue
    gross = num/den
    positions.append({'bot':key[0],'L':L,'gross':gross})

# ---- cohort tagging ----
def cohort(bot):
    if bot=='badday_young_absorb': return 'young'
    if bot=='badday_young_absorb_live': return 'probe'
    if bot and 'flush' in bot and 'live' not in bot: return 'flush'
    return None

for p in positions: p['coh']=cohort(p['bot'])

for c in ('young','flush','probe'):
    ps=[p for p in positions if p['coh']==c]
    if ps:
        g=[p['gross'] for p in ps]
        print(f"{c}: n={len(ps)} meanGross={statistics.mean(g):+.2f} medGross={statistics.median(g):+.2f} medL={statistics.median([p['L'] for p in ps]):.0f}")

# ---- impact model ----
# CPMM: quote reserve Rq = L/2 (liquidity_usd two-sided). impact = 100*(S/Rq)/(1-S/Rq)
def cpmm(S,L):
    Rq=L/2.0
    r=S/Rq
    if r>=0.90: r=0.90   # pool cannot absorb; huge impact
    return 100.0*r/(1.0-r)

BUY_FLOOR=2.73   # structural buy slip @ $5 live (cpmm@5 ~0.02, negligible)
SELL_FLOOR=0.70  # structural sell-into-weakness slip @ $5 live
FEE_LEG=0.17     # $/leg fixed

def net_pnl(g,L,S):
    entry = BUY_FLOOR + cpmm(S,L)
    exit_ = SELL_FLOOR + cpmm(S,L)
    fees  = 2*FEE_LEG/S*100.0
    return g - entry - exit_ - fees

SIZES=[25,50,100,200,400]
print("\n=== NET EV (mean pp) vs SIZE ===")
print(f"{'cohort':8}{'n':>5} " + " ".join(f"${s:>6}" for s in SIZES))
results={}
for c in ('young','flush','probe'):
    ps=[p for p in positions if p['coh']==c]
    if not ps: continue
    # probe gross already has ~$5 friction inside -> add back F=3.43 to get gross decision pnl
    row=[]; medrow=[]
    for S in SIZES:
        nets=[net_pnl(p['gross']+(3.43 if c=='probe' else 0.0), p['L'], S) for p in ps]
        row.append(statistics.mean(nets)); medrow.append(statistics.median(nets))
    results[c]=(len(ps),row,medrow)
    print(f"{c:8}{len(ps):>5} " + " ".join(f"{v:>+6.2f}" for v in row))

print("\n=== NET EV (MEDIAN pp) vs SIZE ===")
print(f"{'cohort':8}{'n':>5} " + " ".join(f"${s:>6}" for s in SIZES))
for c in ('young','flush','probe'):
    if c in results:
        n,row,medrow=results[c]
        print(f"{c:8}{n:>5} " + " ".join(f"{v:>+6.2f}" for v in medrow))

# ---- $/day (mean EV) ----
print("\n=== $/day = meanEV_pp/100 * S * fills/day ===")
for fpd in (6,20):
    print(f"-- fills/day={fpd} --")
    print(f"{'cohort':8} " + " ".join(f"${s:>7}" for s in SIZES))
    for c in ('young','flush','probe'):
        if c in results:
            n,row,_=results[c]
            dph=[v/100.0*S*fpd for v,S in zip(row,SIZES)]
            print(f"{c:8} " + " ".join(f"{v:>+7.1f}" for v in dph))

# ---- thin pool: % positions with S/L > 2% at $200 ----
print("\n=== THIN POOL (S/L>2%) ===")
for c in ('young','flush','probe'):
    ps=[p for p in positions if p['coh']==c]
    if not ps: continue
    for S in (200,400):
        thin=sum(1 for p in ps if S/p['L']>0.02)
        print(f"{c}: ${S} too-thin {thin}/{len(ps)} = {100*thin/len(ps):.1f}%  (L med ${statistics.median([p['L'] for p in ps]):.0f})")

# ---- break-even size per cohort (linear interp where mean net EV crosses 0) ----
print("\n=== break-even size (mean net EV crosses 0) ===")
import numpy as np
for c in ('young','flush','probe'):
    ps=[p for p in positions if p['coh']==c]
    if not ps: continue
    grid=list(range(10,801,2))
    ev=[statistics.mean([net_pnl(p['gross']+(3.43 if c=='probe' else 0.0),p['L'],S) for p in ps]) for S in grid]
    # find zero crossings
    cross=[]
    for i in range(1,len(grid)):
        if (ev[i-1]<=0)!=(ev[i]<=0):
            cross.append(grid[i])
    peak_i=int(np.argmax(ev))
    print(f"{c}: peak meanEV {ev[peak_i]:+.2f}pp @ ${grid[peak_i]}; zero-cross sizes {cross}")

print("\n\n############ SUPPLEMENTARY ############")
# L distribution per cohort
print("\n=== liquidity_usd distribution ===")
for c in ('young','flush','probe'):
    Ls=sorted(p['L'] for p in positions if p['coh']==c)
    if not Ls: continue
    q=lambda f: Ls[int(f*(len(Ls)-1))]
    print(f"{c}: min={Ls[0]:.0f} p10={q(.10):.0f} p25={q(.25):.0f} med={q(.5):.0f} p75={q(.75):.0f} max={Ls[-1]:.0f}")
    for S in (200,400):
        # fraction where impact term alone > 3pp RT (2*cpmm)
        big=sum(1 for L in Ls if 2*cpmm(S,L)>3.0)
        print(f"   ${S}: {100*big/len(Ls):.0f}% of positions have impact>3pp RT")

# FRICTION decomposition at median L
print("\n=== FRICTION breakdown at median L=$38k (pp round trip) ===")
Lmed=38000
print(f"{'S':>6}{'struct':>8}{'impact':>8}{'fees':>7}{'TOTAL':>8}")
for S in [25,50,100,150,200,300,400,500]:
    imp=2*cpmm(S,Lmed); fee=2*FEE_LEG/S*100; struct=3.43
    print(f"{S:>6}{struct:>8.2f}{imp:>8.2f}{fee:>7.2f}{struct+imp+fee:>8.2f}")

# Impact sensitivity: Rq=L (one-sided) vs L/2 (two-sided) at $200
print("\n=== impact sensitivity (2*cpmm RT @ $200, med L) ===")
def cpmm_r(S,L,frac):
    Rq=L*frac; r=S/Rq
    if r>=0.9: r=0.9
    return 100*r/(1-r)
for frac,lbl in ((0.5,'Rq=L/2 (base)'),(1.0,'Rq=L (optimistic)')):
    print(f"  {lbl}: {2*cpmm_r(200,Lmed,frac):.2f}pp")

# $100/day gross-EV requirement
print("\n=== gross EV needed to net $100/day ===")
print(f"{'S':>6}{'fills':>7}{'netEV_need':>11}{'friction':>10}{'GROSS_need':>11}  (current young gross=+1.80)")
for S,f in [(100,20),(150,20),(150,10),(300,10),(500,6),(250,12)]:
    net_need=10000.0/(S*f)
    fric=3.43+2*cpmm(S,Lmed)+2*FEE_LEG/S*100
    print(f"{S:>6}{f:>7}{net_need:>11.2f}{fric:>10.2f}{net_need+fric:>11.2f}")

# Conditional: IF gross lifted so net=0 at $25, does scaling break it?
print("\n=== conditional: hold gross at the $25-breakeven level, scale up ===")
for c in ('young',):
    ps=[p for p in positions if p['coh']==c]
    # find gross shift so mean net EV at $25 = 0
    base=statistics.mean([net_pnl(p['gross'],p['L'],25) for p in ps])
    shift=-base  # add this to every gross to hit 0 at $25
    print(f"{c}: needs +{shift:.2f}pp gross lift to breakeven at $25. Then at larger size:")
    for S in SIZES:
        ev=statistics.mean([net_pnl(p['gross']+shift,p['L'],S) for p in ps])
        print(f"   ${S}: net EV {ev:+.2f}pp")
