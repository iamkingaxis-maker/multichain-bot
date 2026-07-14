import json, os
from datetime import datetime

RD = os.path.dirname(os.path.abspath(__file__))
def J(name): return json.load(open(os.path.join(RD, name)))

pnl = J('wallet_pnl.json'); runners = J('rip_runners_live.json')
meta = J('token_meta.json'); tindex = J('tape_index.json')
wallets = pnl['wallets']; toksym = pnl.get('tok_sym', {})
def asc(s): return ''.join(c if 32 <= ord(c) < 127 else '?' for c in (s or '?'))
def parse_ts(s): return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()

pair2file = {p: v['file'] for p, v in tindex.items()}
tok2pair = {}
for p, v in tindex.items(): tok2pair.setdefault(v['token'], p)

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

meta_age = {}
for p, m in meta.items():
    if m and m.get('pool_created_at'): meta_age[p] = parse_ts(m['pool_created_at'])

# population: every wallet-token with buy_usd>=20 and n_sells>=1 (closed outcome)
rows = []
for w, d in wallets.items():
    for mint, td in d['tokens'].items():
        if td['buy_usd'] < 20 or td['n_sells'] < 1: continue
        if td.get('first_kind') != 'buy': continue  # need entry visible in tape
        pair = tok2pair.get(mint)
        if not pair: continue
        trades = tape(pair)
        b0 = None
        for t in trades:
            if t['maker'] == w and t['kind'] == 'buy': b0 = t; break
        if not b0: continue
        ets = b0['ets']
        win60 = [t for t in trades if ets-60 <= t['ets'] < ets and t['maker'] != w]
        win300 = [t for t in trades if ets-300 <= t['ets'] < ets and t['maker'] != w]
        b60 = [t for t in win60 if t['kind'] == 'buy']
        b300 = [t for t in win300 if t['kind'] == 'buy']
        s300 = [t for t in win300 if t['kind'] == 'sell']
        r = runners.get(mint)
        sells_after = [t for t in trades if t['maker'] == w and t['kind'] == 'sell' and t['ets'] > ets]
        rows.append({
            'w': w, 'mint': mint, 'net': td['covered_net_usd'], 'won': td['covered_net_usd'] > 0,
            'usd0': b0['volume_usd'], 'buy_usd': td['buy_usd'],
            'mfe': (ets - r['ts'])/60 if r and r.get('ts') else None,
            'age_h': (ets - meta_age[pair])/3600 if pair in meta_age else None,
            'nb60': len(b60), 'maxbuy60': max([t['volume_usd'] for t in b60], default=0),
            'nb300': len(b300), 'ns300': len(s300),
            'net300': sum(t['volume_usd'] for t in b300) - sum(t['volume_usd'] for t in s300),
            'maxbuy300': max([t['volume_usd'] for t in b300], default=0),
            'buyers300': len(set(t['maker'] for t in b300)),
            'hold1': (sells_after[0]['ets']-ets)/60 if sells_after else None,
        })

print('population closed positions n =', len(rows))
base_wr = sum(r['won'] for r in rows)/len(rows)
def med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return float('nan')
    n = len(xs)
    return xs[n//2] if n % 2 else (xs[n//2-1]+xs[n//2])/2
print(f'base: WR={base_wr*100:.1f}%  med_net={med([r["net"] for r in rows]):+.2f}  mean_net={sum(r["net"] for r in rows)/len(rows):+.2f}')
print()

def test(name, fn):
    hit = [r for r in rows if fn(r)]
    mis = [r for r in rows if not fn(r)]
    if not hit or not mis: return
    hw = sum(r['won'] for r in hit)/len(hit); mw = sum(r['won'] for r in mis)/len(mis)
    print(f'{name:52s} n={len(hit):5d} WR={hw*100:5.1f}% medN={med([r["net"] for r in hit]):+8.2f} meanN={sum(r["net"] for r in hit)/len(hit):+8.2f} | rest WR={mw*100:5.1f}% medN={med([r["net"] for r in mis]):+7.2f}')

test('T1 big-buy burst: maxbuy60>=75 & nb60>=2', lambda r: r['maxbuy60'] >= 75 and r['nb60'] >= 2)
test('T1b maxbuy60>=100', lambda r: r['maxbuy60'] >= 100)
test('T1c maxbuy300>=150 & buyers300>=4', lambda r: r['maxbuy300'] >= 150 and r['buyers300'] >= 4)
test('T2 early-run: mins_from_event<=360', lambda r: r['mfe'] is not None and r['mfe'] <= 360)
test('T2b mins_from_event<=120', lambda r: r['mfe'] is not None and r['mfe'] <= 120)
test('T2c mins_from_event>1440 (day-old chase)', lambda r: r['mfe'] is not None and r['mfe'] > 1440)
test('T3 young token: age_h<=24', lambda r: r['age_h'] is not None and r['age_h'] <= 24)
test('T3b age_h<=6', lambda r: r['age_h'] is not None and r['age_h'] <= 6)
test('T3c age_h>48', lambda r: r['age_h'] is not None and r['age_h'] > 48)
test('T4 net300>0 (buy-dominant 5m)', lambda r: r['net300'] > 0)
test('T4b net300<-200 (sell-dominant 5m dip)', lambda r: r['net300'] < -200)
test('T5 T1+T2: burst & early', lambda r: r['maxbuy60'] >= 75 and r['nb60'] >= 2 and r['mfe'] is not None and r['mfe'] <= 360)
test('T6 T2+T3: early & young', lambda r: r['mfe'] is not None and r['mfe'] <= 360 and r['age_h'] is not None and r['age_h'] <= 24)
test('T7 burst & young: maxbuy60>=75 & age<=24', lambda r: r['maxbuy60'] >= 75 and r['age_h'] is not None and r['age_h'] <= 24)
test('T8 dip-flow buy: ns300>nb300 & maxbuy300>=100', lambda r: r['ns300'] > r['nb300'] and r['maxbuy300'] >= 100)
print()
# hold-time conditional on entry archetype (exit lens sanity)
fast = [r for r in rows if r['hold1'] is not None and r['hold1'] <= 15]
slow = [r for r in rows if r['hold1'] is not None and r['hold1'] > 15]
print(f'hold<=15m: n={len(fast)} WR={sum(r["won"] for r in fast)/len(fast)*100:.1f}% medN={med([r["net"] for r in fast]):+.2f}')
print(f'hold>15m : n={len(slow)} WR={sum(r["won"] for r in slow)/len(slow)*100:.1f}% medN={med([r["net"] for r in slow]):+.2f}')
print()

# ---- per-entry detail for A/B winner wallets (winning + losing tokens) ----
tiers = {}
for w, d in wallets.items():
    if d['n_pos'] >= 3 and d['covered_net_closed_usd'] > 0: tiers[w] = 'A'
    elif d['n_pos'] >= 2 and d['covered_net_closed_usd'] > 0: tiers[w] = 'B'
byw = {}
for r in rows:
    if r['w'] in tiers: byw.setdefault(r['w'], []).append(r)
print('=== A/B WALLET ENTRIES (closed, from population rows) ===')
print('wal8     tier  sym?     won   net$   usd0  mfe_m  age_h nb60 mxb60 nb300 ns300 net300 mxb300 hold1')
for w in sorted(byw, key=lambda w: tiers[w]):
    for r in sorted(byw[w], key=lambda r: r['mint']):
        sym = asc(toksym.get(r['mint'], '?'))[:8]
        def f(v, wd=6, d=0): return ('--'.rjust(wd) if v is None else f'{v:{wd}.{d}f}')
        print(f"{w[:8]} {tiers[w]:>4} {sym:8s} {'W' if r['won'] else '.':>3} {r['net']:+7.0f} {f(r['usd0'])} {f(r['mfe'])} {f(r['age_h'],6,1)} {f(r['nb60'],4)} {f(r['maxbuy60'],5)} {f(r['nb300'],5)} {f(r['ns300'],5)} {f(r['net300'],6)} {f(r['maxbuy300'],6)} {f(r['hold1'],5,1)}")
