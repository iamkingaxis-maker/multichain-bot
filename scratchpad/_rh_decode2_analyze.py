import json, statistics, collections
from collections import defaultdict

LED='scratchpad/robinhood_tapes/rh_paper_trades.jsonl'
ENTRY=25.0

rows=[]
for line in open(LED,encoding='utf-8'):
    line=line.strip()
    if not line: continue
    try: d=json.loads(line)
    except: continue
    if str(d.get('ts',''))[:4]=='1970': continue
    rows.append(d)

buys=[d for d in rows if d.get('ev')=='buy']
sells=[d for d in rows if d.get('ev')=='sell']
rug={d.get('pool'):d for d in rows if d.get('ev')=='rug_signals'}

# buys indexed by pool, sorted by ts
buys_by_pool=defaultdict(list)
for b in buys:
    buys_by_pool[b.get('pool')].append(b)
for p in buys_by_pool: buys_by_pool[p].sort(key=lambda x:x.get('ts',''))

def entry_for(pool, first_sell_ts):
    cands=[b for b in buys_by_pool.get(pool,[]) if b.get('ts','')<=first_sell_ts]
    if not cands:
        # fallback: nearest buy for pool
        cands=buys_by_pool.get(pool,[])
        if not cands: return None
        return cands[0]
    return cands[-1]

# reconstruct trips per loader logic
sells_by_key=defaultdict(list)
for d in sells:
    sells_by_key[(d.get('bot_id'), d.get('pool'))].append(d)

trips=[]
for (bot,pool),ss in sells_by_key.items():
    ss.sort(key=lambda x:x.get('ts',''))
    cur=[]
    for s in ss:
        cur.append(s)
        if s.get('fully'):
            pnl=sum((x.get('pnl_usd') or 0.0) for x in cur)
            first_sell_ts=cur[0].get('ts','')
            ent=entry_for(pool, first_sell_ts)
            kinds=[x.get('kind') for x in cur]
            trips.append({
                'bot':bot or 'rh_young_v1','pool':pool,
                'ret':pnl/ENTRY*100.0,
                'dip':(ent or {}).get('dip_pct'),
                'liq':(ent or {}).get('liq'),
                'last_kind':cur[-1].get('kind'),
                'kinds':kinds,
                'n_legs':len(cur),
                'sell_ts':cur[-1].get('ts',''),
                'first_sell_ts':first_sell_ts,
                'rug':rug.get(pool),
            })
            cur=[]

def pct(vals,q):
    if not vals: return None
    vals=sorted(vals);
    import math
    i=q*(len(vals)-1); lo=int(i); hi=min(lo+1,len(vals)-1)
    return vals[lo]+(vals[hi]-vals[lo])*(i-lo)

def ex_top2_median(ts):
    # per-token realized (sum ret per pool), then drop top-2 pools, median
    by_tok=defaultdict(float)
    for t in ts: by_tok[t['pool']]+=t['ret']
    vals=sorted(by_tok.values(), reverse=True)
    ex2=vals[2:] if len(vals)>2 else vals
    return (statistics.median(ex2) if ex2 else None,
            statistics.median(vals) if vals else None,
            len(by_tok))

by_bot=defaultdict(list)
for t in trips: by_bot[t['bot']].append(t)

print("=== PER-BOT SUMMARY (trip-level) ===")
print(f"{'bot':24} {'nTrip':>5} {'nTok':>4} {'tokmedEx2':>9} {'tokmedAll':>9} {'retMed':>7} {'green%':>6} {'dipMed':>7} {'liqMed':>9}")
order=sorted(by_bot,key=lambda b:-ex_top2_median(by_bot[b])[0] if ex_top2_median(by_bot[b])[0] is not None else 99)
for b in order:
    ts=by_bot[b]
    ex2,alltok,ntok=ex_top2_median(ts)
    rets=[t['ret'] for t in ts]
    dips=[t['dip'] for t in ts if t['dip'] is not None]
    liqs=[t['liq'] for t in ts if t['liq'] is not None]
    green=sum(1 for r in rets if r>0)/len(rets)*100
    print(f"{b:24} {len(ts):>5} {ntok:>4} "
          f"{(ex2 if ex2 is not None else 0):>9.2f} {(alltok if alltok is not None else 0):>9.2f} "
          f"{statistics.median(rets):>7.2f} {green:>5.0f}% "
          f"{(statistics.median(dips) if dips else 0):>7.1f} {(statistics.median(liqs) if liqs else 0):>9.0f}")

print()
print("=== EXIT-KIND MIX per bot (last leg kind) ===")
for b in order:
    ts=by_bot[b]
    c=collections.Counter(t['last_kind'] for t in ts)
    print(f"{b:24} n={len(ts):>3} "+ " ".join(f"{k}:{v}" for k,v in c.most_common()))
