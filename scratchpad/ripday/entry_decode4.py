import json, os
from datetime import datetime

RD = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(RD, 'entry_decode_pop.json'))) if os.path.exists(os.path.join(RD, 'entry_decode_pop.json')) else None
if rows is None:
    # rebuild population rows (same as v3)
    def J(name): return json.load(open(os.path.join(RD, name)))
    pnl = J('wallet_pnl.json'); runners = J('rip_runners_live.json')
    meta = J('token_meta.json'); tindex = J('tape_index.json')
    wallets = pnl['wallets']
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
            out.sort(key=lambda t: t['ets']); tape_cache[pair] = out
        return tape_cache[pair]
    meta_age = {p: parse_ts(m['pool_created_at']) for p, m in meta.items() if m and m.get('pool_created_at')}
    rows = []
    for w, d in wallets.items():
        for mint, td in d['tokens'].items():
            if td['buy_usd'] < 20 or td['n_sells'] < 1 or td.get('first_kind') != 'buy': continue
            pair = tok2pair.get(mint)
            if not pair: continue
            trades = tape(pair)
            b0 = next((t for t in trades if t['maker'] == w and t['kind'] == 'buy'), None)
            if not b0: continue
            ets = b0['ets']
            win60 = [t for t in trades if ets-60 <= t['ets'] < ets and t['maker'] != w]
            win300 = [t for t in trades if ets-300 <= t['ets'] < ets and t['maker'] != w]
            b60 = [t for t in win60 if t['kind'] == 'buy']
            b300 = [t for t in win300 if t['kind'] == 'buy']; s300 = [t for t in win300 if t['kind'] == 'sell']
            r = runners.get(mint)
            rows.append({'w': w, 'mint': mint, 'net': td['covered_net_usd'], 'won': td['covered_net_usd'] > 0,
                'mfe': (ets - r['ts'])/60 if r and r.get('ts') else None,
                'age_h': (ets - meta_age[pair])/3600 if pair in meta_age else None,
                'nb60': len(b60), 'ns60': len(win60)-len(b60),
                'maxbuy60': max([t['volume_usd'] for t in b60], default=0),
                'nb300': len(b300), 'ns300': len(s300),
                'net300': sum(t['volume_usd'] for t in b300) - sum(t['volume_usd'] for t in s300),
                'maxbuy300': max([t['volume_usd'] for t in b300], default=0),
                'sell300': sum(t['volume_usd'] for t in s300),
                'buyers300': len(set(t['maker'] for t in b300))})
    json.dump(rows, open(os.path.join(RD, 'entry_decode_pop.json'), 'w'))

def med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return float('nan')
    n = len(xs)
    return xs[n//2] if n % 2 else (xs[n//2-1]+xs[n//2])/2

def test(name, fn):
    hit = [r for r in rows if fn(r)]
    if len(hit) < 8: print(f'{name:58s} n={len(hit)} too small'); return
    hw = sum(r['won'] for r in hit)/len(hit)
    toks = {}
    for r in hit: toks[r['mint']] = toks.get(r['mint'], 0) + 1
    wt = sorted(set(r['mint'] for r in hit if r['won']))
    print(f'{name:58s} n={len(hit):4d} WR={hw*100:5.1f}% medN={med([r["net"] for r in hit]):+8.2f} meanN={sum(r["net"] for r in hit)/len(hit):+8.2f} tok={len(toks)} winTok={len(wt)} top1tok={max(toks.values())}')

print('n=', len(rows), ' baseWR=', f"{sum(r['won'] for r in rows)/len(rows)*100:.1f}")
print()
print('-- absorption (flush + big buyer) variants --')
test('A1 net300<=-200 & maxbuy60>=75', lambda r: r['net300'] <= -200 and r['maxbuy60'] >= 75)
test('A2 net300<=-200 & maxbuy300>=100', lambda r: r['net300'] <= -200 and r['maxbuy300'] >= 100)
test('A3 sell300>=300 & maxbuy60>=75', lambda r: r['sell300'] >= 300 and r['maxbuy60'] >= 75)
test('A4 sell300>=300 & maxbuy60>=75 & age<=24h', lambda r: r['sell300'] >= 300 and r['maxbuy60'] >= 75 and (r['age_h'] or 99) <= 24)
test('A5 net300<=-200 & maxbuy60<75 (flush no whale)', lambda r: r['net300'] <= -200 and r['maxbuy60'] < 75)
print()
print('-- burst-follow variants --')
test('B1 maxbuy60>=75 & nb60>=2 (any flow)', lambda r: r['maxbuy60'] >= 75 and r['nb60'] >= 2)
test('B2 B1 & net300>0 (buy-dominant chase)', lambda r: r['maxbuy60'] >= 75 and r['nb60'] >= 2 and r['net300'] > 0)
test('B3 B1 & net300<=0', lambda r: r['maxbuy60'] >= 75 and r['nb60'] >= 2 and r['net300'] <= 0)
test('B4 maxbuy60>=200', lambda r: r['maxbuy60'] >= 200)
test('B5 maxbuy60>=200 & net300<=0', lambda r: r['maxbuy60'] >= 200 and r['net300'] <= 0)
print()
print('-- timing overlays --')
test('C1 A2 & mfe<=480', lambda r: r['net300'] <= -200 and r['maxbuy300'] >= 100 and (r['mfe'] or 9e9) <= 480)
test('C2 A2 & age<=24', lambda r: r['net300'] <= -200 and r['maxbuy300'] >= 100 and (r['age_h'] or 99) <= 24)
test('C3 B3 & mfe<=480', lambda r: r['maxbuy60'] >= 75 and r['nb60'] >= 2 and r['net300'] <= 0 and (r['mfe'] or 9e9) <= 480)
test('C4 maxbuy60>=75 & mfe<=360 (orig T5 loosened)', lambda r: r['maxbuy60'] >= 75 and (r['mfe'] or 9e9) <= 360)
test('C5 maxbuy60>=75 & nb60>=2 & mfe<=360', lambda r: r['maxbuy60'] >= 75 and r['nb60'] >= 2 and (r['mfe'] or 9e9) <= 360)
print()
print('-- decile scans --')
for k in ['maxbuy60', 'net300', 'sell300']:
    vals = sorted(r[k] for r in rows)
    qs = [vals[int(len(vals)*q/10)] for q in range(0, 10)]
    print(k, 'deciles:', [f'{v:.0f}' for v in qs])
    for lo_i in range(0, 10, 2):
        lo = qs[lo_i]; hi = qs[lo_i+2] if lo_i+2 < 10 else float('inf')
        sub = [r for r in rows if lo <= r[k] < hi]
        if sub:
            print(f'  [{lo:9.0f},{hi if hi!=float("inf") else 999999:9.0f}) n={len(sub):4d} WR={sum(r["won"] for r in sub)/len(sub)*100:5.1f}% medN={med([r["net"] for r in sub]):+7.2f} meanN={sum(r["net"] for r in sub)/len(sub):+8.2f}')
