import json, glob, os
from datetime import datetime

RD = os.path.dirname(os.path.abspath(__file__))
def L(p): return json.load(open(os.path.join(RD,p)))
def ep(s): return datetime.fromisoformat(s).timestamp()

wp = L('wallet_pnl.json'); tok_sym = wp['tok_sym']; wallets = wp['wallets']
prelim = L('winners_prelim.json')['winners']
POS = []
for pw in prelim:
    w = pw['wallet']; rec = wallets[w]
    net = sum(d['covered_net_usd'] for m,d in rec['tokens'].items() if d['buy_usd']>=20 and d['n_sells']>0)
    if net > 0: POS.append(w)

# legs for positive wallets
legs = {}
for f in glob.glob(os.path.join(RD,'tape_*.jsonl')):
    for line in open(f, encoding='utf-8'):
        t = json.loads(line)
        if t['maker'] not in POS: continue
        legs.setdefault((t['maker'], t['token']), []).append((ep(t['ts']), t['kind'], t['volume_usd']))
for k in legs: legs[k].sort()

# conservative capped net: chronological, sells credited only up to 2x cumulative in-tape buys so far
print('CONSERVATIVE CAPPED NET (sells credited max 2x cum in-tape buys; generous +100% gain allowance)')
print('%-13s %9s %9s' % ('wallet','naive$','capped$'))
capped_results = {}
for w in POS:
    naive = 0.0; capped = 0.0
    for (ww, m), ls in legs.items():
        if ww != w: continue
        buys = sum(x[2] for x in ls if x[1]=='buy')
        if buys < 20: continue
        cum_b = 0.0; cum_s_capped = 0.0; cum_s = 0.0; seen_buy = False
        for ts, kind, usd in ls:
            if kind == 'buy':
                cum_b += usd; seen_buy = True
            else:
                if not seen_buy: continue
                cum_s += usd
                cum_s_capped = min(cum_s, 2.0*cum_b)
        naive += cum_s - cum_b if cum_s>0 else -cum_b
        capped += cum_s_capped - cum_b if cum_s>0 else -cum_b
    capped_results[w] = (naive, capped)
    print('%-13s %9.2f %9.2f' % (w[:12], naive, capped))

# duplicate-operator detection among positives: same token set + similar buy sizes
print()
print('DUPLICATE-OPERATOR CLUSTERS (same tokens traded, first-leg ts within 120s of each other)')
sig = {}
for w in POS:
    toks = {}
    for (ww, m), ls in legs.items():
        if ww != w: continue
        if sum(x[2] for x in ls if x[1]=='buy') >= 20:
            toks[m] = ls[0][0]
    sig[w] = toks
done = set()
for i, a in enumerate(POS):
    for b in POS[i+1:]:
        shared = set(sig[a]) & set(sig[b])
        if len(shared) >= 2:
            close = sum(1 for m in shared if abs(sig[a][m]-sig[b][m]) < 120)
            if close >= 2:
                print('  CLUSTER: %s + %s shared=%d close_ts=%d' % (a[:12], b[:12], len(shared), close))

# ---------------- mechanism check on recon ----------------
print()
print('MECHANISM CHECK: all recon buys with fwd_coverage>=60min, grouped by pos_in_prior90m_range')
rows = []
for line in open(os.path.join(RD,'rip_recon.jsonl')):
    r = json.loads(line)
    if r.get('fwd_coverage_mins',0) >= 60 and r.get('pos_in_prior90m_range') is not None:
        rows.append(r)
print('n rows with >=60min fwd coverage: %d' % len(rows))
def stats(rs, name):
    if not rs:
        print('  %-22s n=0' % name); return
    n = len(rs)
    med = lambda a: sorted(a)[len(a)//2]
    hi = [r['fwd_hi90_pct'] for r in rs]; lo = [r['fwd_low90_pct'] for r in rs]
    # policy sim: TP +12 / SL -10 using order low-first? no order info; pessimistic: SL first if both hit
    win = sum(1 for r in rs if r['fwd_hi90_pct']>=12 and r['fwd_low90_pct']>-10)
    loss = sum(1 for r in rs if r['fwd_low90_pct']<=-10)
    flat = n - win - loss
    pess_ev = (win*12 + loss*-10 + sum(min(max(r['fwd_hi90_pct'],r['fwd_low90_pct']),0) for r in rs if r['fwd_hi90_pct']<12 and r['fwd_low90_pct']>-10)/max(flat,1)*0 )/n
    print('  %-22s n=%3d med_fwd_hi90=%+6.1f med_fwd_lo90=%+6.1f  TP12/SL10 pess: win=%d loss=%d flat=%d ev~%+.1f%%' % (
        name, n, med(hi), med(lo), win, loss, flat, (win*12 - loss*10)/n))
stats([r for r in rows if r['pos_in_prior90m_range']<=0.33], 'DIP (rng<=0.33)')
stats([r for r in rows if 0.33<r['pos_in_prior90m_range']<0.66], 'MID (0.33-0.66)')
stats([r for r in rows if r['pos_in_prior90m_range']>=0.66], 'BREAKOUT (rng>=0.66)')
print()
print('same, restricted to mins_from_event 30..360:')
w360 = [r for r in rows if 30 <= r.get('mins_from_event', -1) <= 360]
stats([r for r in w360 if r['pos_in_prior90m_range']<=0.33], 'DIP (rng<=0.33)')
stats([r for r in w360 if 0.33<r['pos_in_prior90m_range']<0.66], 'MID (0.33-0.66)')
stats([r for r in w360 if r['pos_in_prior90m_range']>=0.66], 'BREAKOUT (rng>=0.66)')
print()
print('dip-depth from prior 90m high (dip_from_high = -prior90m_high_vs_entry):')
for lo_b, hi_b, name in [(0,10,'shallow 0-10%'),(10,25,'mid 10-25%'),(25,100,'deep >25%')]:
    stats([r for r in rows if lo_b <= r['prior90m_high_vs_entry_pct'] < hi_b], 'below prior high '+name)
