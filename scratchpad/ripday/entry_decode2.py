import json, os, random
from datetime import datetime

RD = os.path.dirname(os.path.abspath(__file__))
def J(name): return json.load(open(os.path.join(RD, name)))

pnl = J('wallet_pnl.json'); runners = J('rip_runners_live.json')
meta = J('token_meta.json'); tindex = J('tape_index.json')
wallets = pnl['wallets']; toksym = pnl.get('tok_sym', {})

def asc(s): return ''.join(c if 32 <= ord(c) < 127 else '?' for c in (s or '?'))

tiers = {}
for w, d in wallets.items():
    if d['n_pos'] >= 3 and d['covered_net_closed_usd'] > 0: tiers[w] = 'A'
    elif d['n_pos'] >= 2 and d['covered_net_closed_usd'] > 0: tiers[w] = 'B'
    elif d['n_pos'] >= 3: tiers[w] = 'SPRAY'
    elif d['n_pos'] == 0 and d['n_neg'] >= 2 and d['n_sells'] if False else False: pass
# LOSER baseline: 0 pos, >=2 neg closed tokens
losers = [w for w, d in wallets.items() if d['n_pos'] == 0 and d['n_neg'] >= 2]
random.seed(7)
for w in random.sample(losers, min(120, len(losers))): tiers.setdefault(w, 'LOSER')

pair2file = {p: v['file'] for p, v in tindex.items()}
tok2pair = {}
for p, v in tindex.items(): tok2pair.setdefault(v['token'], p)

def parse_ts(s): return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()

tape_cache = {}
def tape(pair):
    if pair not in tape_cache:
        out = []
        for line in open(os.path.join(RD, pair2file[pair]), encoding='utf-8'):
            line = line.strip()
            if line:
                t = json.loads(line); t['ets'] = parse_ts(t['ts']); out.append(t)
        out.sort(key=lambda t: t['ets'])
        tape_cache[pair] = out
    return tape_cache[pair]

ohlc = {}
for fn in os.listdir(RD):
    if fn.startswith('ohlc_') and fn.endswith('.json'):
        try:
            o = json.load(open(os.path.join(RD, fn)))
            if o.get('bars'): ohlc[o['token']] = o
        except Exception: pass

def flow_feat(trades, ets, wallet):
    """order-flow in window before ets, excluding wallet's own trades"""
    f = {}
    for wname, secs in [('60s', 60), ('300s', 300)]:
        win = [t for t in trades if ets - secs <= t['ets'] < ets and t['maker'] != wallet]
        b = [t for t in win if t['kind'] == 'buy']; s = [t for t in win if t['kind'] == 'sell']
        f['nb_' + wname] = len(b); f['ns_' + wname] = len(s)
        f['netusd_' + wname] = sum(t['volume_usd'] for t in b) - sum(t['volume_usd'] for t in s)
        f['buyers_' + wname] = len(set(t['maker'] for t in b))
        f['maxbuy_' + wname] = max([t['volume_usd'] for t in b], default=0)
    # tape density check: any trade in prior 300s at all (coverage)
    f['tape_covered_pre'] = any(ets - 300 <= t['ets'] < ets for t in trades)
    return f

def ohlc_feat(bars, ets):
    idx = None
    for i, b in enumerate(bars):
        if b[0] <= ets < b[0] + 60: idx = i; break
    if idx is None: return None
    px = bars[idx][4]
    if not px: return None
    t0 = bars[idx][0]
    pri90 = [b for b in bars[:idx] if b[0] >= t0 - 90*60]
    f = {'px': px}
    if len(pri90) >= 10:
        hi = max(b[2] for b in pri90); lo = min(b[3] for b in pri90)
        f['pos_range'] = (px - lo)/(hi - lo) if hi > lo else 0.5
        f['pri90_hi_pct'] = (hi/px - 1)*100
    if idx >= 4 and bars[idx-4][4]: f['ret3m'] = (bars[idx-1][4]/bars[idx-4][4]-1)*100
    if idx >= 16 and bars[idx-16][4]: f['ret15m'] = (bars[idx-1][4]/bars[idx-16][4]-1)*100
    pre = bars[:idx]
    if pre:
        pk = max(b[2] for b in pre)
        f['below_peak_pre_pct'] = (px/pk - 1)*100
        f['mins_since_peak'] = (t0 - max(pre, key=lambda b: b[2])[0])/60
    post = bars[idx+1:]
    if post:
        f['fwd_max_pct'] = (max(b[2] for b in post)/px - 1)*100
        f['fwd_cov_mins'] = (post[-1][0]-t0)/60
    return f

rows = []
for w, tier in tiers.items():
    d = wallets[w]
    for mint, td in d['tokens'].items():
        if td['buy_usd'] < 20: continue
        pair = tok2pair.get(mint)
        if not pair: continue
        trades = tape(pair)
        wt = [t for t in trades if t['maker'] == w]
        buys = [t for t in wt if t['kind'] == 'buy']
        sells = [t for t in wt if t['kind'] == 'sell']
        if not buys: continue
        b0 = buys[0]; ets = b0['ets']
        s_after = [s for s in sells if s['ets'] > ets]
        row = {'wallet': w, 'tier': tier, 'mint': mint, 'sym': asc(td.get('sym') or toksym.get(mint, '?')),
               'ts': b0['ts'], 'buy_usd': td['buy_usd'], 'usd0': b0['volume_usd'],
               'n_buys': len(buys), 'n_sells': len(sells), 'net': td['covered_net_usd'],
               'won': td['covered_net_usd'] > 0,
               'hold1_m': (s_after[0]['ets']-ets)/60 if s_after else None,
               'holdlast_m': (s_after[-1]['ets']-ets)/60 if s_after else None}
        if len(buys) > 1: row['adds_span_m'] = (buys[-1]['ets']-ets)/60
        m = meta.get(pair)
        if m and m.get('pool_created_at'): row['age_h'] = (ets - parse_ts(m['pool_created_at']))/3600
        r = runners.get(mint)
        if r and r.get('ts'): row['mins_from_event'] = (ets - r['ts'])/60
        ti = tindex[pair]
        row['tape_trunc'] = (ets - parse_ts(ti['oldest'])) < 600
        row.update(flow_feat(trades, ets, w))
        o = ohlc.get(mint)
        if o:
            f = ohlc_feat(o['bars'], ets)
            if f: row.update(f)
        rows.append(row)

json.dump(rows, open(os.path.join(RD, 'entry_decode_rows2.json'), 'w'), indent=1, default=str)

def med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return None
    n = len(xs)
    return xs[n//2] if n % 2 else (xs[n//2-1]+xs[n//2])/2

def fmt(v, w=8, d=2):
    if v is None: return '--'.rjust(w)
    if isinstance(v, bool): return ('Y' if v else 'n').rjust(w)
    try: return f'{v:{w}.{d}f}'
    except Exception: return str(v)[:w].rjust(w)

KEYS = ['age_h', 'mins_from_event', 'nb_60s', 'ns_60s', 'netusd_60s', 'buyers_60s', 'maxbuy_60s',
        'nb_300s', 'ns_300s', 'netusd_300s', 'buyers_300s', 'maxbuy_300s',
        'pos_range', 'ret3m', 'ret15m', 'below_peak_pre_pct', 'mins_since_peak',
        'fwd_max_pct', 'hold1_m', 'holdlast_m', 'buy_usd', 'usd0', 'n_buys']

print('rows total:', len(rows))
groups = [('A', True), ('A', False), ('B', True), ('B', False),
          ('SPRAY', True), ('SPRAY', False), ('LOSER', False)]
print()
print('MEDIANS BY GROUP (tier/outcome)')
hdr = 'metric'.ljust(20) + ''.join(f'{t}_{"W" if won else "L"}'.rjust(10) for t, won in groups)
print(hdr)
for k in KEYS:
    line = k.ljust(20)
    for t, won in groups:
        sub = [r.get(k) for r in rows if r['tier'] == t and r['won'] == won]
        line += fmt(med(sub), 10, 2)
    print(line)
print()
print('group ns: ' + ', '.join(f'{t}_{"W" if won else "L"}={len([r for r in rows if r["tier"]==t and r["won"]==won])}' for t, won in groups))
# pre-buy tape coverage rates
for t, won in groups:
    sub = [r for r in rows if r['tier'] == t and r['won'] == won]
    cov = len([r for r in sub if r.get('tape_covered_pre')])
    print(f'{t}_{"W" if won else "L"}: pre-buy tape covered {cov}/{len(sub)}')
