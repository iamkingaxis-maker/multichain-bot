import json, statistics
rows=json.load(open('_vf_rows.json'))
def scrub(rs): return [r for r in rs if not((r['pnl'] or 0)>0 and (r['hold'] or 0)<10)]
s=scrub([r for r in rows if r['bsl'] is not None])
live=[r for r in s if (r['bot'] or '').endswith('_live')]
print('live-money rows (bsl known, scrubbed):',len(live),'bots:',set(r['bot'] for r in live))
def wr(rs): n=len(rs); return (100*sum(1 for r in rs if (r['pnl'] or 0)>0)/n if n else 0),n
def med(rs): v=[r['pnl'] for r in rs]; return round(statistics.median(v),2) if v else None
lk=[r for r in live if r['bsl']<10]; lc=[r for r in live if 10<=r['bsl']<=35]
for name,rs in [('live bsl<10',lk),('live bsl10-35',lc)]:
    w,n=wr(rs); print(f'{name:16s} WR={w:5.1f}% n={n:3d} pairs={len(set(r["pair"] for r in rs))} med={med(rs)}')
# real slippage on live fills
slips=[r['entry_slip'] for r in live if r['entry_slip'] is not None]
print('live entry_slip median',round(statistics.median(slips),2),'p90',round(sorted(slips)[int(0.9*len(slips))],2),'n',len(slips))
