import json, statistics
d=json.load(open('scratchpad/_full_trades.json'))

# Build ordered join: sells join to PRIOR buy of same token
# We'll walk in list order; but need chronological. Sort by time.
def gt(t): return t.get('time') or ''
d_sorted = sorted(d, key=gt)

last_buy = {}   # token -> entry_meta + time
trips = []      # joined round trips
for t in d_sorted:
    tok = t.get('token')
    if t.get('type')=='buy':
        last_buy[tok] = t
    elif t.get('type')=='sell':
        b = last_buy.get(tok)
        if b is None: continue
        em = b.get('entry_meta') or {}
        pnl = t.get('pnl_pct')
        if pnl is None: continue
        # SCRUB trivial round trips: ret>0 & hold<10s
        # compute hold from times if available
        hold=None
        try:
            from datetime import datetime
            bt=datetime.fromisoformat(b['time']); st=datetime.fromisoformat(t['time'])
            hold=(st-bt).total_seconds()
        except: pass
        if pnl>0 and hold is not None and hold<10:
            continue
        trips.append({
            'token':tok,'pnl':pnl,'pc_h6':em.get('pc_h6'),'pc_h1':em.get('pc_h1'),
            'pc_h24':em.get('pc_h24'),'vol':em.get('token_volatility_h24_pct'),
            'time':t.get('time'),'address':b.get('address'),
        })

print('total joined trips (scrubbed):', len(trips))
tw=[x for x in trips if x['pc_h6'] is not None]
print('trips with pc_h6:', len(tw))

def stats(rows):
    n=len(rows)
    if n==0: return (0,None,None,0,0)
    pnls=[r['pnl'] for r in rows]
    wr=100*sum(1 for p in pnls if p>0)/n
    avg=sum(pnls)/n
    dt=len(set(r['token'] for r in rows))
    return (n,round(wr,1),round(avg,3),dt,round(sum(pnls),1))

allrows=tw
print('ALL   n,wr,avg,distinct,sumpnl:', stats(allrows))

cell=[x for x in tw if -25 < x['pc_h6'] <= -10]
below=[x for x in tw if x['pc_h6'] <= -25]
above=[x for x in tw if x['pc_h6'] > -10]
print('CELL -25<h6<=-10:', stats(cell))
print('BELOW h6<=-25   :', stats(below))
print('ABOVE h6>-10    :', stats(above))

# winners/losers in cell
w=[x for x in cell if x['pnl']>0]; l=[x for x in cell if x['pnl']<=0]
print('cell winners n,sumpp:', len(w), round(sum(x['pnl'] for x in w),1))
print('cell losers  n,sumpp:', len(l), round(sum(x['pnl'] for x in l),1))

# Book EV effect of removing cell
allpnl=[x['pnl'] for x in tw]
kept=[x['pnl'] for x in tw if not (-25 < x['pc_h6'] <= -10)]
print('book EV all:', round(sum(allpnl)/len(allpnl),3), 'n', len(allpnl))
print('book EV kept:', round(sum(kept)/len(kept),3), 'n', len(kept))
print('delta pp/trade:', round(sum(kept)/len(kept) - sum(allpnl)/len(allpnl),4))
