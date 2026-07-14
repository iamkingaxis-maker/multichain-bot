import json, os
RD = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(RD, 'entry_decode_pop.json')))
pnl = json.load(open(os.path.join(RD, 'wallet_pnl.json')))
wallets = pnl['wallets']; toksym = pnl.get('tok_sym', {})
def asc(s): return ''.join(c if 32 <= ord(c) < 127 else '?' for c in (s or '?'))

tiers = {}
for w, d in wallets.items():
    if d['n_pos'] >= 3 and d['covered_net_closed_usd'] > 0: tiers[w] = 'A'
    elif d['n_pos'] >= 2 and d['covered_net_closed_usd'] > 0: tiers[w] = 'B'
    elif d['n_pos'] >= 3: tiers[w] = 'SPRAY'

def arch(r):
    if r['net300'] <= -100 and r['maxbuy60'] >= 75: return '1_flush_absorb'
    if r['maxbuy60'] >= 75 and r['net300'] > -100: return '2_whale_burst'
    if r['nb300'] + r['ns300'] <= 2: return '3_quiet_tape'
    if r['net300'] <= -100: return '4_flush_no_whale'
    if r['nb300'] >= 4 and r['net300'] > 0: return '5_crowd_chase'
    return '0_other'

def med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return float('nan')
    n = len(xs)
    return xs[n//2] if n % 2 else (xs[n//2-1]+xs[n//2])/2

print('=== POPULATION BY ARCHETYPE (n=686 closed positions) ===')
print(f"{'archetype':18s} {'n':>5} {'WR%':>6} {'medNet':>8} {'meanNet':>8} {'medMFE':>7} {'medAge':>7}")
for a in sorted(set(arch(r) for r in rows)):
    sub = [r for r in rows if arch(r) == a]
    print(f'{a:18s} {len(sub):5d} {sum(r["won"] for r in sub)/len(sub)*100:6.1f} {med([r["net"] for r in sub]):+8.2f} {sum(r["net"] for r in sub)/len(sub):+8.2f} {med([r["mfe"] for r in sub]):7.0f} {med([r["age_h"] for r in sub]):7.1f}')

print()
print('=== A/B WINNER-WALLET ENTRIES BY ARCHETYPE ===')
for grp, cond in [('A/B winning tokens', lambda r: tiers.get(r['w']) in ('A','B') and r['won']),
                  ('A/B losing tokens', lambda r: tiers.get(r['w']) in ('A','B') and not r['won']),
                  ('SPRAY winning', lambda r: tiers.get(r['w']) == 'SPRAY' and r['won']),
                  ('SPRAY losing', lambda r: tiers.get(r['w']) == 'SPRAY' and not r['won'])]:
    sub = [r for r in rows if cond(r)]
    cnt = {}
    for r in sub: cnt[arch(r)] = cnt.get(arch(r), 0) + 1
    tot = len(sub)
    dist = '  '.join(f'{a.split("_",1)[1]}={c}({c/tot*100:.0f}%)' for a, c in sorted(cnt.items()))
    print(f'{grp:20s} n={tot:3d}: {dist}')

print()
print('=== ARCHETYPE x MFE (population) ===')
for a in ['1_flush_absorb', '2_whale_burst']:
    for lo, hi in [(0, 360), (360, 1440), (1440, 99999)]:
        sub = [r for r in rows if arch(r) == a and r['mfe'] is not None and lo <= r['mfe'] < hi]
        if len(sub) >= 5:
            print(f'{a} mfe[{lo:5d},{hi:5d}) n={len(sub):4d} WR={sum(r["won"] for r in sub)/len(sub)*100:5.1f}% medN={med([r["net"] for r in sub]):+7.2f} meanN={sum(r["net"] for r in sub)/len(sub):+8.2f}')

# ---- rip_recon cross-check: price-path position of winner entries ----
print()
print('=== rip_recon.jsonl cross-check (OHLC price-path features) ===')
recon = [json.loads(l) for l in open(os.path.join(RD, 'rip_recon.jsonl'), encoding='utf-8') if l.strip()]
def outcome(w, tok):
    d = wallets.get(w)
    if not d: return None
    td = d['tokens'].get(tok)
    if not td or td['n_sells'] < 1: return None
    return td['covered_net_usd'] > 0
lab = []
for r in recon:
    o = outcome(r['wallet'], r['token'])
    if o is None: continue
    if r.get('fwd_coverage_mins', 0) is not None: pass
    lab.append((r, o))
print('recon rows with closed outcome:', len(lab))
for name, cond in [('won', True), ('lost', False)]:
    sub = [r for r, o in lab if o == cond]
    if not sub: continue
    print(f'-- {name} n={len(sub)}')
    for k in ['pos_in_prior90m_range', 'prior90m_high_vs_entry_pct', 'prior90m_low_vs_entry_pct',
              'mins_from_event', 'fwd_max6h_pct', 'fwd_low15_pct', 'usd']:
        vals = [r.get(k) for r in sub]
        print(f'   {k:28s} med={med(vals):+9.2f} n={len([v for v in vals if v is not None])}')
# dip-buy fraction
for name, cond in [('won', True), ('lost', False)]:
    sub = [r for r, o in lab if o == cond and r.get('pos_in_prior90m_range') is not None]
    if not sub: continue
    lowq = len([r for r in sub if r['pos_in_prior90m_range'] <= 0.35])
    hiq = len([r for r in sub if r['pos_in_prior90m_range'] >= 0.75])
    print(f'{name}: pos<=0.35 {lowq}/{len(sub)} ({lowq/len(sub)*100:.0f}%), pos>=0.75 {hiq}/{len(sub)} ({hiq/len(sub)*100:.0f}%)')
