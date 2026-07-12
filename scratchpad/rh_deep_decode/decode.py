import json, statistics as st
from collections import defaultdict, Counter

BUYS=defaultdict(list)   # bot -> list of buy dicts
SELLS=defaultdict(list)  # bot -> list of sell dicts (ordered)
for l in open('scratchpad/robinhood_tapes/rh_paper_trades.jsonl'):
    d=json.loads(l)
    b=d.get('bot_id')
    if not b: continue
    if d['ev']=='buy': BUYS[b].append(d)
    elif d['ev']=='sell': SELLS[b].append(d)

def med(xs): return round(st.median(xs),2) if xs else None
def mean(xs): return round(st.mean(xs),2) if xs else None
def pctl(xs,p):
    if not xs: return None
    xs=sorted(xs); i=int(round((p/100)*(len(xs)-1))); return round(xs[i],2)

# reconstruct trips per bot: sequential episodes closed on fully==True
def trips(bot):
    tr=[]; cur=[]; 
    for s in sorted(SELLS[bot], key=lambda x:x['ts']):
        cur.append(s)
        if s.get('fully'):
            tr.append(cur); cur=[]
    if cur: tr.append(cur)
    return tr

FOCUS=['rh_deep_only','rh_bites2','rh_f_arc_scalp','rh_demand_heavy','rh_wide_ladder','rh_moonbag','rh_young_v1']
print(f"{'bot':20} {'nbuy':>4} {'dipMed':>6} {'dipP25':>6} {'ageMed':>6} {'liqMed':>7} | {'ntr':>3} {'trMean':>6} {'trMed':>6} {'trStd':>6} {'win%':>5} {'holdMed':>7}")
allkinds=defaultdict(Counter)
for bot in FOCUS:
    dips=[b['dip_pct'] for b in BUYS[bot] if b.get('dip_pct') is not None]
    ages=[b['age_h'] for b in BUYS[bot] if b.get('age_h') is not None]
    liqs=[b['liq'] for b in BUYS[bot] if b.get('liq') is not None]
    tr=trips(bot)
    trpnl=[round(sum(s.get('pnl_usd',0) or 0 for s in t),3) for t in tr]
    holds=[]
    for t in tr:
        for s in t:
            if s.get('fully'): 
                pass
    # hold: use last leg reason? sells have no hold_s in jsonl; approximate via ts span not available per-trip easily
    wins=sum(1 for x in trpnl if x>0)
    trstd=round(st.pstdev(trpnl),2) if len(trpnl)>1 else 0
    for t in tr:
        for s in t: allkinds[bot][s['kind']]+=1
    print(f"{bot:20} {len(BUYS[bot]):>4} {med(dips)!s:>6} {pctl(dips,25)!s:>6} {med(ages)!s:>6} {med(liqs)!s:>7} | {len(tr):>3} {mean(trpnl)!s:>6} {med(trpnl)!s:>6} {trstd!s:>6} {round(100*wins/len(trpnl)) if trpnl else 0:>4}% ")
print()
print("=== exit KIND mix per bot (leg counts) ===")
for bot in FOCUS:
    tot=sum(allkinds[bot].values())
    if not tot: print(f"{bot:20} (no local sells)"); continue
    row=" ".join(f"{k}:{v}" for k,v in allkinds[bot].most_common())
    print(f"{bot:20} n={tot:3} {row}")
