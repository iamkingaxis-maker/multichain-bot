#!/usr/bin/env python3
"""Forward-data reachability check: for recent distinct tokens, pull GT 1m bars and
compare stale A (entry_mid_price) and current B (entry_price) to the realized
post-fill price path. Was A ever revisited after we decided (reachable), or is it
below the live market (stale illusion)?"""
import json, datetime as dt, asyncio, sys
sys.path.insert(0,'.')
from feeds.gecko_ohlcv import GeckoTerminalClient

d=json.load(open('_full_trades.json'))
buys=[r for r in d if r.get('type')=='buy']
seen={}
for b in sorted(buys,key=lambda r:r.get('time',''),reverse=True):
    a=b.get('address')
    if a and a not in seen and b.get('pair_address') and b.get('entry_mid_price') and b.get('entry_price'):
        seen[a]=b
recent=list(seen.values())[:45]   # most-recent 45 distinct tokens

async def main():
    gt=GeckoTerminalClient(rate_per_min=25)
    out=[]
    for b in recent:
        pair=b['pair_address']; A=b['entry_mid_price']; B=b['entry_price']
        ft=dt.datetime.fromisoformat(b['time']); fts=ft.timestamp()
        try:
            cs=await gt.fetch_1m(pair, limit=1000, cache_ttl_override=0)
        except Exception as e:
            cs=[]
        if not cs:
            out.append((b['token'],None)); continue
        # coverage: does data cover fill time?
        omin=min(c.open_time for c in cs); omax=max(c.open_time for c in cs)
        if fts < omin-60:  # fill before data window
            out.append((b['token'],'no_cov')); continue
        # fill candle = candle whose [open_time, open_time+60) contains fts
        fillc=[c for c in cs if c.open_time<=fts<c.open_time+60]
        # forward window candles (fill candle + next 10 min)
        fwd=[c for c in cs if c.open_time>=(int(fts//60)*60) and c.open_time<=fts+600]
        if not fwd:
            out.append((b['token'],'no_fwd')); continue
        fwd_min=min(c.low for c in fwd)      # lowest reachable price in 10min after fill
        fwd_min2=min(c.low for c in fwd[:2]) # ~2 min window
        fc=fillc[0] if fillc else fwd[0]
        out.append((b['token'],{
            'A':A,'B':B,'driftpct':(B/A-1)*100,
            'fill_low':fc.low,'fill_high':fc.high,
            'fwd_min_10m':fwd_min,'fwd_min_2m':fwd_min2,
            # is A reachable? A >= forward min (price dipped to/below A after fill)
            'A_reach_10m':A>=fwd_min, 'A_reach_2m':A>=fwd_min2,
            'B_vs_fwdmin_pct':(B/fwd_min-1)*100,  # how far B above best reachable 10m
            'A_below_fillrange':(A<fc.low),
        }))
    return out

res=asyncio.run(main())
import statistics
ok=[(t,r) for t,r in res if isinstance(r,dict)]
print('tokens sampled:',len(res),' with forward coverage:',len(ok))
nocov=sum(1 for t,r in res if r in ('no_cov','no_fwd',None))
print('no coverage:',nocov)
if ok:
    a10=sum(1 for t,r in ok if r['A_reach_10m'])
    a2=sum(1 for t,r in ok if r['A_reach_2m'])
    abr=sum(1 for t,r in ok if r['A_below_fillrange'])
    print('\nA (stale snapshot) reachable within 10min after fill: %d/%d (%.0f%%)'%(a10,len(ok),100*a10/len(ok)))
    print('A reachable within ~2min after fill:                   %d/%d (%.0f%%)'%(a2,len(ok),100*a2/len(ok)))
    print('A below the fill-minute candle low (stale/illusion):   %d/%d (%.0f%%)'%(abr,len(ok),100*abr/len(ok)))
    bvf=[r['B_vs_fwdmin_pct'] for t,r in ok]
    print('B above best-reachable-10m-low: median=%.2f%% mean=%.2f%%'%(statistics.median(bvf),statistics.mean(bvf)))
    dr=[r['driftpct'] for t,r in ok]
    print('A->B drift (sample): median=%.2f%% mean=%.2f%%'%(statistics.median(dr),statistics.mean(dr)))
    print('\nsample rows (token, A, B, fwd_min_10m, A_reach_10m):')
    for t,r in ok[:15]:
        print('  %-14s A=%.3g B=%.3g fwdmin=%.3g A_reach=%s'%(t[:14],r['A'],r['B'],r['fwd_min_10m'],r['A_reach_10m']))
