import json, math
from collections import defaultdict, Counter
from datetime import datetime

d = json.load(open('_trades_cache.json'))

# filter badday family
rows = [e for e in d if str(e.get('bot_id','')).startswith('badday')]
buys = [e for e in rows if e.get('type')=='buy']
sells = [e for e in rows if e.get('type')=='sell']

def ts(e):
    t = e.get('time')
    try:
        return datetime.fromisoformat(t.replace('Z','+00:00'))
    except Exception:
        return None

# index buys by (bot,token) sorted by time
buy_idx = defaultdict(list)
for b in buys:
    buy_idx[(b['bot_id'], b['token'])].append(b)
for k in buy_idx:
    buy_idx[k].sort(key=lambda e: ts(e) or datetime.min)

# match each sell (sorted by time) to earliest unmatched prior buy FIFO
sells_sorted = sorted(sells, key=lambda e: ts(e) or datetime.min)
used = defaultdict(int)  # pointer per key
pairs = []
for s in sells_sorted:
    k = (s['bot_id'], s['token'])
    bl = buy_idx.get(k, [])
    st = ts(s)
    # find earliest buy at pointer that is <= sell time
    ptr = used[k]
    match = None
    while ptr < len(bl):
        bt = ts(bl[ptr])
        if bt is not None and st is not None and bt <= st:
            match = bl[ptr]
            used[k] = ptr+1
            break
        ptr += 1
        used[k] = ptr
    if match is None:
        # fallback: match on entry_price closeness
        for i,b in enumerate(bl):
            if abs((b.get('entry_price') or 0)-(s.get('entry_price') or -1)) < 1e-18:
                match = b; break
    if match is not None:
        pairs.append((match, s))

print('badday buys', len(buys), 'sells', len(sells), 'pairs', len(pairs))

# build records
WINDOW = datetime.fromisoformat('2026-07-03T00:00:00+00:00')
recs = []
for b, s in pairs:
    bt = ts(b)
    if bt is None or bt < WINDOW:
        continue
    em = b.get('entry_meta') or {}
    def g(k):
        v = em.get(k)
        return v
    imb = g('net_flow_5m_imbalance')
    nf5 = g('net_flow_5m_usd')
    nf60 = g('net_flow_60s_usd')
    imb15 = g('net_flow_15s_imbalance')
    nrb = g('n_recurring_buyers_3plus')
    lbv = g('large_buyer_volume_pct')
    if imb is None or nf5 is None or nf60 is None:
        continue
    peak = s.get('peak_pnl_pct')
    pnl_pct = s.get('pnl_pct')
    hold = s.get('hold_secs')
    if peak is None or pnl_pct is None or hold is None:
        continue
    # SCRUB: drop ret>0 & hold<10s
    if pnl_pct > 0 and hold < 10:
        continue
    # acceleration = nf60 / (nf5/5)
    denom = (nf5/5.0)
    accel = (nf60/denom) if denom not in (0,None) and abs(denom)>1e-9 else None
    recs.append({
        'bot': b['bot_id'], 'token': b['token'], 'time': b['time'], 'ts': bt.timestamp(),
        'day': bt.toordinal(),
        'imb': imb, 'nf5': nf5, 'nf60': nf60, 'imb15': imb15,
        'nrb': nrb if nrb is not None else 0, 'lbv': lbv if lbv is not None else 0.0,
        'accel': accel,
        'peak': peak, 'pnl_pct': pnl_pct, 'hold': hold,
        'pc_h1': em.get('pc_h1'), 'pc_h6': em.get('pc_h6'), 'pc_h24': em.get('pc_h24'),
    })

print('records in window (scrubbed):', len(recs))
json.dump(recs, open('scratchpad/_dsg_recs.json','w'))

# reproduce confirmed separation
W = [r for r in recs if r['peak'] >= 20]
L = [r for r in recs if r['peak'] < 6]
def mean(xs):
    xs=[x for x in xs if x is not None]
    return sum(xs)/len(xs) if xs else float('nan')
def med(xs):
    xs=sorted(x for x in xs if x is not None)
    n=len(xs)
    return xs[n//2] if n else float('nan')
print('\n=== REPRODUCE confirmed separation ===')
print(f'winners(peak>=20) n={len(W)}  losers(peak<6) n={len(L)}')
for f in ['imb','nf5','accel','nf60','imb15','nrb','lbv']:
    print(f'  {f:6s} W_mean={mean([r[f] for r in W]):.4g}  L_mean={mean([r[f] for r in L]):.4g}  W_med={med([r[f] for r in W]):.4g}  L_med={med([r[f] for r in L]):.4g}')

# outcome distribution
print('\npeak distribution:', Counter(int(r["peak"]//5)*5 for r in recs))
print('total recs', len(recs), 'winners', len(W), 'losers', len(L), 'middle', len(recs)-len(W)-len(L))
print('bots:', Counter(r['bot'] for r in recs))
