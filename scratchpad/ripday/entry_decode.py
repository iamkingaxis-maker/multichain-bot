import json, os, math
from datetime import datetime, timezone

RD = os.path.dirname(os.path.abspath(__file__))
def J(name): return json.load(open(os.path.join(RD, name)))

pnl = J('wallet_pnl.json')
runners = J('rip_runners_live.json')
meta = J('token_meta.json')
tindex = J('tape_index.json')
toksym = pnl.get('tok_sym', {})
wallets = pnl['wallets']

# ---- winner tiers ----
tiers = {}
for w, d in wallets.items():
    if d['n_pos'] >= 3 and d['covered_net_closed_usd'] > 0: tiers[w] = 'A'
    elif d['n_pos'] >= 2 and d['covered_net_closed_usd'] > 0: tiers[w] = 'B'
    elif d['n_pos'] >= 3: tiers[w] = 'SPRAY'

# ---- load ohlc ----
ohlc = {}
for fn in os.listdir(RD):
    if fn.startswith('ohlc_') and fn.endswith('.json'):
        try:
            o = json.load(open(os.path.join(RD, fn)))
            if o.get('bars'): ohlc[o['token']] = o
        except Exception: pass

# pair -> (token, file)
pair2file = {p: v['file'] for p, v in tindex.items()}
tok2pair = {}
for p, v in tindex.items(): tok2pair.setdefault(v['token'], p)

def parse_ts(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()

# preload tapes for needed pairs only
def load_tape(pair):
    f = os.path.join(RD, pair2file[pair])
    out = []
    for line in open(f, encoding='utf-8'):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out

tape_cache = {}
def tape(pair):
    if pair not in tape_cache: tape_cache[pair] = load_tape(pair)
    return tape_cache[pair]

def feat(bars, ets):
    """bars: [[t,o,h,l,c,v],...] asc; ets: entry epoch. returns dict or None"""
    idx = None
    for i, b in enumerate(bars):
        if b[0] <= ets < b[0] + 60: idx = i; break
    if idx is None:
        # allow within-gap match: last bar before ets if gap < 5 min
        prev = [i for i, b in enumerate(bars) if b[0] <= ets]
        if not prev: return None
        idx = prev[-1]
        if ets - bars[idx][0] > 300: return None
    px = bars[idx][4]
    if not px: return None
    t0 = bars[idx][0]
    pri90 = [b for b in bars[:idx] if b[0] >= t0 - 90*60]
    f = {'idx': idx, 'px': px, 'bar_t': t0, 'n_pri90': len(pri90)}
    if pri90:
        hi = max(b[2] for b in pri90); lo = min(b[3] for b in pri90)
        f['pri90_hi_pct'] = (hi/px - 1) * 100
        f['pri90_lo_pct'] = (lo/px - 1) * 100
        f['pos_range'] = (px - lo) / (hi - lo) if hi > lo else 0.5
        f['range_width'] = (hi/lo - 1) * 100 if lo > 0 else None
    # momentum
    if idx >= 4 and bars[idx-4][4]:
        f['ret3m'] = (bars[idx-1][4] / bars[idx-4][4] - 1) * 100
    if idx >= 16 and bars[idx-16][4]:
        f['ret15m'] = (bars[idx-1][4] / bars[idx-16][4] - 1) * 100
    # vol expansion: last 5 bars vs prior 60-bar baseline
    if idx >= 15:
        v5 = sum(b[5] for b in bars[max(0, idx-5):idx])
        base = [b[5] for b in bars[max(0, idx-65):idx-5]]
        if base and sum(base) > 0:
            f['volx'] = v5 / (sum(base)/len(base) * 5)
        f['v5_usd'] = v5
    # run frame
    pre = bars[:idx]
    post = bars[idx+1:]
    if pre:
        peak_pre = max(b[2] for b in pre); low_pre = min(b[3] for b in pre)
        f['below_peak_pre_pct'] = (px/peak_pre - 1) * 100
        f['frac_of_pre_range'] = (px - low_pre)/(peak_pre - low_pre) if peak_pre > low_pre else 0.5
        # minutes since pre-entry peak
        pt = max(pre, key=lambda b: b[2])[0]
        f['mins_since_peak'] = (t0 - pt)/60
    if post:
        peak_post = max(b[2] for b in post)
        f['fwd_max_pct'] = (peak_post/px - 1) * 100
        f['fwd_min_pct'] = (min(b[3] for b in post)/px - 1) * 100
        f['fwd_cov_mins'] = (post[-1][0] - t0)/60
        if pre:
            f['before_peak'] = peak_post > peak_pre
    return f

rows = []
for w, tier in tiers.items():
    d = wallets[w]
    for mint, td in d['tokens'].items():
        if td['buy_usd'] < 20: continue
        pair = tok2pair.get(mint)
        if not pair: continue
        o = ohlc.get(mint)
        bars = o['bars'] if o else None
        # wallet's trades on this token from tape
        wt = [t for t in tape(pair) if t['maker'] == w]
        buys = sorted([t for t in wt if t['kind'] == 'buy'], key=lambda t: t['ts'])
        sells = sorted([t for t in wt if t['kind'] == 'sell'], key=lambda t: t['ts'])
        if not buys: continue
        b0 = buys[0]
        ets = parse_ts(b0['ts'])
        # hold time to first sell after first buy
        s_after = [s for s in sells if parse_ts(s['ts']) > ets]
        hold = (parse_ts(s_after[0]['ts']) - ets)/60 if s_after else None
        full_exit = (parse_ts(sells[-1]['ts']) - ets)/60 if s_after else None
        row = {
            'wallet': w, 'tier': tier, 'mint': mint, 'sym': td.get('sym') or toksym.get(mint, '?'),
            'ts': b0['ts'], 'usd0': b0['volume_usd'], 'buy_usd': td['buy_usd'],
            'n_buys': len(buys), 'net': td['covered_net_usd'], 'won': td['covered_net_usd'] > 0,
            'hold_first_sell_m': hold, 'hold_last_sell_m': full_exit, 'n_sells': len(sells),
        }
        # add-buy spacing
        if len(buys) > 1:
            row['adds_span_m'] = (parse_ts(buys[-1]['ts']) - ets)/60
        # token age
        m = meta.get(pair)
        if m and m.get('pool_created_at'):
            row['age_h'] = (ets - parse_ts(m['pool_created_at']))/3600
        r = runners.get(mint)
        if r and r.get('ts'):
            row['mins_from_event'] = (ets - r['ts'])/60
        # tape truncation flag: is first in-tape buy near tape start?
        ti = tindex[pair]
        row['tape_trunc'] = (ets - parse_ts(ti['oldest'])) < 600
        if bars:
            f = feat(bars, ets)
            if f: row.update(f)
        rows.append(row)

json.dump(rows, open(os.path.join(RD, 'entry_decode_rows.json'), 'w'), indent=1, default=str)

# ---- summary printing ----
def fmt(v, w=7, d=1):
    if v is None: return ' ' * (w-2) + '--'
    if isinstance(v, bool): return ('Y' if v else 'n').rjust(w)
    try: return f'{v:{w}.{d}f}'
    except Exception: return str(v)[:w].rjust(w)

def med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return None
    n = len(xs)
    return xs[n//2] if n % 2 else (xs[n//2-1]+xs[n//2])/2

print('rows total:', len(rows))
for tier in ['A', 'B', 'SPRAY']:
    for won in [True, False]:
        sub = [r for r in rows if r['tier'] == tier and r['won'] == won]
        if not sub: continue
        print(f'--- tier {tier} won={won} n={len(sub)} ---')
        for k in ['age_h', 'mins_from_event', 'pos_range', 'pri90_hi_pct', 'pri90_lo_pct',
                  'range_width', 'ret3m', 'ret15m', 'volx', 'below_peak_pre_pct',
                  'mins_since_peak', 'fwd_max_pct', 'hold_first_sell_m', 'buy_usd', 'n_buys']:
            vals = [r.get(k) for r in sub]
            n_ok = len([v for v in vals if v is not None])
            print(f'  {k:22s} med={fmt(med(vals),8,2)} n={n_ok}')

# detailed per-buy table for tier A+B winning entries
print()
print('=== TIER A/B WINNING ENTRIES (per first buy) ===')
hdr = ['wal8', 'sym', 'ts', 'usd', 'net', 'age_h', 'm_evt', 'posR', 'r3m', 'r15m', 'volx',
       'blwPk', 'sincePk', 'fwdMax', 'hold1', 'nB', 'trunc']
print(' '.join(h.rjust(8) for h in hdr))
for r in sorted(rows, key=lambda r: (r['tier'], r['wallet'], r['ts'])):
    if r['tier'] in ('A', 'B') and r['won']:
        print(' '.join([
            r['wallet'][:8], (r['sym'] or '?')[:8].rjust(8), r['ts'][5:16],
            fmt(r['buy_usd'], 8, 0), fmt(r['net'], 8, 0), fmt(r.get('age_h'), 8, 1),
            fmt(r.get('mins_from_event'), 8, 0), fmt(r.get('pos_range'), 8, 2),
            fmt(r.get('ret3m'), 8, 1), fmt(r.get('ret15m'), 8, 1), fmt(r.get('volx'), 8, 1),
            fmt(r.get('below_peak_pre_pct'), 8, 1), fmt(r.get('mins_since_peak'), 8, 0),
            fmt(r.get('fwd_max_pct'), 8, 1), fmt(r.get('hold_first_sell_m'), 8, 1),
            fmt(r.get('n_buys'), 8, 0), fmt(r.get('tape_trunc'), 6),
        ]))
print()
print('=== TIER A/B LOSING ENTRIES ===')
for r in sorted(rows, key=lambda r: (r['tier'], r['wallet'], r['ts'])):
    if r['tier'] in ('A', 'B') and not r['won']:
        print(' '.join([
            r['wallet'][:8], (r['sym'] or '?')[:8].rjust(8), r['ts'][5:16],
            fmt(r['buy_usd'], 8, 0), fmt(r['net'], 8, 0), fmt(r.get('age_h'), 8, 1),
            fmt(r.get('mins_from_event'), 8, 0), fmt(r.get('pos_range'), 8, 2),
            fmt(r.get('ret3m'), 8, 1), fmt(r.get('ret15m'), 8, 1), fmt(r.get('volx'), 8, 1),
            fmt(r.get('below_peak_pre_pct'), 8, 1), fmt(r.get('mins_since_peak'), 8, 0),
            fmt(r.get('fwd_max_pct'), 8, 1), fmt(r.get('hold_first_sell_m'), 8, 1),
            fmt(r.get('n_buys'), 8, 0), fmt(r.get('tape_trunc'), 6),
        ]))
