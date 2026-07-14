#!/usr/bin/env python3
"""Wider reachability check via GT 5m bars (~3.5d retention). For each recent distinct
token compare stale A and current B to the post-fill 5m low path."""
import json, datetime as dt, asyncio, sys, statistics
sys.path.insert(0,'.')
from feeds.gecko_ohlcv import GeckoTerminalClient

d=json.load(open('_full_trades.json'))
buys=[r for r in d if r.get('type')=='buy']
seen={}
for b in sorted(buys,key=lambda r:r.get('time',''),reverse=True):
    a=b.get('address')
    if a and a not in seen and b.get('pair_address') and b.get('entry_mid_price') and b.get('entry_price'):
        seen[a]=b
recent=list(seen.values())[:90]

async def main():
    gt=GeckoTerminalClient(rate_per_min=25)
    out=[]
    for b in recent:
        pair=b['pair_address']; A=b['entry_mid_price']; B=b['entry_price']
        ft=dt.datetime.fromisoformat(b['time']); fts=ft.timestamp()
        try:
            cs=await gt.fetch_5m(pair, limit=1000, cache_ttl_override=0)
        except Exception:
            cs=[]
        if not cs: out.append((b['token'],None)); continue
        omin=min(c.open_time for c in cs)
        if fts < omin-300: out.append((b['token'],'no_cov')); continue
        bucket=int(fts//300)*300
        fwd=[c for c in cs if bucket<=c.open_time<=fts+900]  # fill bucket + ~15min
        if not fwd: out.append((b['token'],'no_fwd')); continue
        fc=fwd[0]
        fwd_min=min(c.low for c in fwd)
        out.append((b['token'],{
            'A':A,'B':B,'driftpct':(B/A-1)*100,
            'fill_low':fc.low,'fill_high':fc.high,
            'fwd_min_15m':fwd_min,
            'A_reach_15m':A>=fwd_min,
            'A_below_fillrange':A<fc.low,
            'B_vs_fwdmin_pct':(B/fwd_min-1)*100,
            'A_vs_fwdmin_pct':(A/fwd_min-1)*100,
        }))
    return out

res=asyncio.run(main())
ok=[(t,r) for t,r in res if isinstance(r,dict)]
print('sampled:',len(res),' forward-cov:',len(ok),' no-cov:',sum(1 for t,r in res if r in ('no_cov','no_fwd',None)))
if ok:
    a15=sum(1 for t,r in ok if r['A_reach_15m'])
    abr=sum(1 for t,r in ok if r['A_below_fillrange'])
    print('A reachable within ~15min after fill: %d/%d (%.0f%%)'%(a15,len(ok),100*a15/len(ok)))
    print('A below fill-bucket low (stale below mkt): %d/%d (%.0f%%)'%(abr,len(ok),100*abr/len(ok)))
    bvf=[r['B_vs_fwdmin_pct'] for t,r in ok]
    avf=[r['A_vs_fwdmin_pct'] for t,r in ok]
    print('B above best-reachable-15m-low: median=%.2f%% mean=%.2f%%'%(statistics.median(bvf),statistics.mean(bvf)))
    print('A vs best-reachable-15m-low:    median=%.2f%% mean=%.2f%% (neg=A below reachable=unreachable)'%(statistics.median(avf),statistics.mean(avf)))
    dr=[r['driftpct'] for t,r in ok]
    print('A->B drift (sample): median=%.2f%% mean=%.2f%%'%(statistics.median(dr),statistics.mean(dr)))
