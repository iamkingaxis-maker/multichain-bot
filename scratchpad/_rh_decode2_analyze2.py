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
buys_by_pool=defaultdict(list)
for b in buys: buys_by_pool[b.get('pool')].append(b)
for p in buys_by_pool: buys_by_pool[p].sort(key=lambda x:x.get('ts',''))
def entry_for(pool, fst):
    cands=[b for b in buys_by_pool.get(pool,[]) if b.get('ts','')<=fst]
    if not cands:
        cands=buys_by_pool.get(pool,[]); return cands[0] if cands else None
    return cands[-1]
sells_by_key=defaultdict(list)
for d in sells: sells_by_key[(d.get('bot_id'), d.get('pool'))].append(d)
trips=[]
for (bot,pool),ss in sells_by_key.items():
    ss.sort(key=lambda x:x.get('ts',''))
    cur=[]
    for i,s in enumerate(ss):
        cur.append(s)
        if s.get('fully'):
            pnl=sum((x.get('pnl_usd') or 0.0) for x in cur)
            ent=entry_for(pool,cur[0].get('ts',''))
            r=rug.get(pool) or {}
            trips.append({'bot':bot or 'rh_young_v1','pool':pool,'ret':pnl/ENTRY*100.0,
                'dip':(ent or {}).get('dip_pct'),'liq':(ent or {}).get('liq'),
                'last_kind':cur[-1].get('kind'),'sell_ts':cur[-1].get('ts',''),
                'top10':r.get('top10_pct'),'n_holders':r.get('n_holders'),
                'pool_pct':r.get('pool_pct_of_supply'),'idx':i})
            cur=[]
by_bot=defaultdict(list)
for t in trips: by_bot[t['bot']].append(t)

def ex2(ts):
    d=defaultdict(float)
    for t in ts: d[t['pool']]+=t['ret']
    v=sorted(d.values(),reverse=True); e=v[2:] if len(v)>2 else v
    return (statistics.median(e) if e else None, len(d))

def desc(name, ts):
    if not ts: print(f"{name}: n=0"); return
    rets=[t['ret'] for t in ts]
    e,ntok=ex2(ts)
    green=sum(1 for r in rets if r>0)/len(rets)*100
    print(f"{name:34} nTrip={len(ts):>3} nTok={ntok:>2} ex2={ (e if e is not None else 0):>7.2f} retMed={statistics.median(rets):>6.2f} green={green:>3.0f}%")

print("### WINNER vs LOSER entry conditions (demand_heavy, deep_only) ###")
for b in ['rh_demand_heavy','rh_deep_only','rh_young_v1']:
    ts=by_bot[b]
    W=[t for t in ts if t['ret']>0]; L=[t for t in ts if t['ret']<=0]
    def md(xs,k):
        v=[t[k] for t in xs if t[k] is not None]; return statistics.median(v) if v else None
    print(f"\n{b}: n={len(ts)}  W={len(W)} L={len(L)}")
    for lbl,xs in [('WIN',W),('LOSE',L)]:
        print(f"  {lbl}: dip={md(xs,'dip')}, liq={md(xs,'liq')}, top10={md(xs,'top10')}, holders={md(xs,'n_holders')}, poolpct={md(xs,'pool_pct')}")

print("\n### DEPTH as a bot-INDEPENDENT lever (pool all dip-mode scalp trips) ###")
scalp_bots={'rh_young_v1','rh_deep_only','rh_demand_heavy','rh_wide_ladder','rh_moonbag','rh_bites2','rh_first_touch','rh_liq40'}
pooled=[t for t in trips if t['bot'] in scalp_bots and t['dip'] is not None]
for lo,hi,lbl in [(-999,-25,'deep <=-25'),(-25,-18,'-25..-18'),(-18,-12,'-18..-12'),(-12,999,'shallow >-12')]:
    sub=[t for t in pooled if lo>t['dip']>=hi] if False else [t for t in pooled if hi<=t['dip']<lo]
    desc(f"  dip {lbl}", sub)

print("\n### OOS split: odd/even trip index per bot (deterministic within-bot order by sell_ts) ###")
for b in ['rh_demand_heavy','rh_deep_only']:
    ts=sorted(by_bot[b],key=lambda x:x['sell_ts'])
    odd=[t for i,t in enumerate(ts) if i%2==1]; even=[t for i,t in enumerate(ts) if i%2==0]
    print(f"\n{b}:")
    desc("  ODD trips", odd); desc("  EVEN trips", even)

print("\n### demand_heavy: split by DIP depth (is deeper better within heavy-demand?) ###")
dh=by_bot['rh_demand_heavy']
desc("  dh dip<=-18 (deeper)", [t for t in dh if t['dip'] is not None and t['dip']<=-18])
desc("  dh dip>-18 (shallower)", [t for t in dh if t['dip'] is not None and t['dip']>-18])

print("\n### combined proxy: deep_only trips that ALSO would pass higher demand? (no demand stamp - N/A) ###")
print("  NOTE: buys do not stamp demand-$; cannot retro-build deep&heavy-demand cohort. Flagged.")
