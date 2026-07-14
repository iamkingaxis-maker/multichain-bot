import json, os
from datetime import datetime
RD = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(RD, 'entry_decode_pop.json')))
pnl = json.load(open(os.path.join(RD, 'wallet_pnl.json')))
wallets = pnl['wallets']

def arch1(r): return r['net300'] <= -100 and r['maxbuy60'] >= 75
hit = [r for r in rows if arch1(r)]
wins = sorted([r['net'] for r in hit], reverse=True)
print('ARCH1 n=%d totalNet=%+.0f' % (len(hit), sum(wins)))
print('top nets:', [f'{v:+.0f}' for v in wins[:8]])
print('bottom  :', [f'{v:+.0f}' for v in wins[-8:]])
ex_top = sum(wins[1:]) / (len(wins)-1)
print(f'mean excl top1 = {ex_top:+.2f}; median = {wins[len(wins)//2]:+.2f}')
pos50 = len([v for v in wins if v > 50]); neg50 = len([v for v in wins if v < -50])
print(f'|net|>50: +{pos50} / -{neg50}')
# distinct wallets
print('distinct wallets in ARCH1 hits:', len(set(r['w'] for r in hit)))

# first-buy timestamps by day (need tape re-scan: rebuild minimal ts)
# use entry_decode_rows2.json which has ts for tier rows only; instead re-derive per row via wallet_pnl first_ts
def day(r):
    td = wallets[r['w']]['tokens'][r['mint']]
    return td['first_ts'][:10]
from collections import Counter
c = Counter(day(r) for r in hit)
print('ARCH1 hits by first_ts day:', dict(c))
for d in sorted(c):
    sub = [r for r in hit if day(r) == d]
    print(f'  {d}: n={len(sub)} WR={sum(r["won"] for r in sub)/len(sub)*100:.0f}% meanN={sum(r["net"] for r in sub)/len(sub):+.2f}')

# ---- signal-level forward returns: join recon rows to flow features ----
recon = [json.loads(l) for l in open(os.path.join(RD, 'rip_recon.jsonl'), encoding='utf-8') if l.strip()]
tindex = json.load(open(os.path.join(RD, 'tape_index.json')))
pair2file = {p: v['file'] for p, v in tindex.items()}
def parse_ts(s): return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
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

def med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return float('nan')
    n = len(xs)
    return xs[n//2] if n % 2 else (xs[n//2-1]+xs[n//2])/2

joined = []
for r in recon:
    if r.get('src') != 'io_tape' or r['pair'] not in pair2file: continue
    if r.get('fwd_coverage_mins') is None or r['fwd_coverage_mins'] < 60: continue
    ets = parse_ts(r['ts'])
    trades = tape(r['pair'])
    w300 = [t for t in trades if ets-300 <= t['ets'] < ets and t['maker'] != r['wallet']]
    b60 = [t for t in w300 if t['kind'] == 'buy' and t['ets'] >= ets-60]
    b3 = [t for t in w300 if t['kind'] == 'buy']; s3 = [t for t in w300 if t['kind'] == 'sell']
    net300 = sum(t['volume_usd'] for t in b3) - sum(t['volume_usd'] for t in s3)
    maxbuy60 = max([t['volume_usd'] for t in b60], default=0)
    nb300 = len(b3)
    r2 = dict(r); r2['net300'] = net300; r2['maxbuy60'] = maxbuy60; r2['nb300'] = nb300
    joined.append(r2)
print()
print('recon rows with fwd_coverage>=60m and tape flow:', len(joined))
def show(name, sub):
    if len(sub) < 5: print(f'{name}: n={len(sub)} too small'); return
    print(f'{name:44s} n={len(sub):3d} fwdMax6h med={med([r["fwd_max6h_pct"] for r in sub]):+6.2f} fwdHi90 med={med([r.get("fwd_hi90_pct") for r in sub]):+6.2f} fwdLow15 med={med([r["fwd_low15_pct"] for r in sub]):+6.2f} fwdLow30 med={med([r.get("fwd_low30_pct") for r in sub]):+6.2f} posRange med={med([r.get("pos_in_prior90m_range") for r in sub]):+5.2f}')
show('ALL', joined)
show('ARCH1 flush-absorb (net300<=-100 & maxbuy60>=75)', [r for r in joined if r['net300'] <= -100 and r['maxbuy60'] >= 75])
show('dip-position only (posRange<=0.35)', [r for r in joined if (r.get('pos_in_prior90m_range') or 1) <= 0.35])
show('dip-pos + maxbuy60>=75', [r for r in joined if (r.get('pos_in_prior90m_range') or 1) <= 0.35 and r['maxbuy60'] >= 75])
show('breakout chase (posRange>=0.75)', [r for r in joined if (r.get('pos_in_prior90m_range') or 0) >= 0.75])
show('crowd chase (nb300>=4 & net300>0)', [r for r in joined if r['nb300'] >= 4 and r['net300'] > 0])
# TP/stop simulation on ARCH1-like signal rows: hit +8 before -8?
def race(sub, tp, st):
    ok = 0; n = 0
    for r in sub:
        hi90 = r.get('fwd_hi90_pct'); lo90 = r.get('fwd_low90_pct')
        if hi90 is None or lo90 is None: continue
        n += 1
        # order unknown within 90m: conservative = stop first if both hit
        if hi90 >= tp and lo90 > -st: ok += 1
    return ok, n
for name, sub in [('ARCH1', [r for r in joined if r['net300'] <= -100 and r['maxbuy60'] >= 75]),
                  ('dip+whale', [r for r in joined if (r.get('pos_in_prior90m_range') or 1) <= 0.35 and r['maxbuy60'] >= 75]),
                  ('ALL', joined)]:
    ok, n = race(sub, 8, 8)
    if n: print(f'{name}: hit +8% within 90m WITHOUT ever dipping -8% (conservative): {ok}/{n} = {ok/n*100:.0f}%')
