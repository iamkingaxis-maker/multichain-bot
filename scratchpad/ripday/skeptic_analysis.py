import json, glob, os, sys
from datetime import datetime, timezone

RD = os.path.dirname(os.path.abspath(__file__))
def L(p): return json.load(open(os.path.join(RD,p)))

wp = L('wallet_pnl.json'); tok_sym = wp['tok_sym']; wallets = wp['wallets']
prelim = L('winners_prelim.json')['winners']
union = {w['wallet']: w for w in L('candidate_wallets_union.json')['wallets']}
meta = L('token_meta.json')          # pair -> {pool_created_at,...}
runners = L('rip_runners_live.json') # mint -> {pair, ts(event), ...}
try: runners.update({k:v for k,v in L('rip_runners.json').items() if k not in runners})
except Exception: pass

# recon lines by wallet
recon = {}
for line in open(os.path.join(RD,'rip_recon.jsonl')):
    r = json.loads(line)
    recon.setdefault(r['wallet'], []).append(r)

# load all tapes; index trades by (wallet, token)
trades = {}  # (wallet, token) -> list of (ts_epoch, kind, usd)
pair_of_token = {}
for f in glob.glob(os.path.join(RD,'tape_*.jsonl')):
    for line in open(f, encoding='utf-8'):
        t = json.loads(line)
        ts = datetime.fromisoformat(t['ts']).timestamp()
        trades.setdefault((t['maker'], t['token']), []).append((ts, t['kind'], t['volume_usd']))
        pair_of_token[t['token']] = t['pair']
for k in trades: trades[k].sort()

def pool_created_epoch(token):
    pair = pair_of_token.get(token) or (runners.get(token) or {}).get('pair')
    m = meta.get(pair)
    if not m or not m.get('pool_created_at'): return None
    return datetime.fromisoformat(m['pool_created_at'].replace('Z','+00:00')).timestamp()

# candidates: all prelim wallets (n_pos>=2) -- we re-verify everything
out = []
for pw in prelim:
    w = pw['wallet']; rec = wallets[w]
    toks = rec['tokens']
    # recompute strict: profitable = covered_net_usd>0 and buy_usd>=20 and n_sells>0
    pos = [(m,d) for m,d in toks.items() if d['buy_usd']>=20 and d['n_sells']>0 and d['covered_net_usd']>0]
    neg = [(m,d) for m,d in toks.items() if d['buy_usd']>=20 and d['n_sells']>0 and d['covered_net_usd']<=0]
    openb = [(m,d) for m,d in toks.items() if d['buy_usd']>=20 and d['n_sells']==0]
    net = sum(d['covered_net_usd'] for m,d in pos+neg)
    ntr = len(pos)+len(neg)
    wr = 100.0*len(pos)/ntr if ntr else 0.0

    # per-token micro behavior
    snip_secs = []   # first buy age vs pool creation
    holds = []       # first buy -> last sell, minutes (closed toks)
    min_gaps = []    # min gap between consecutive legs same token
    buy_sizes = []
    nlegs = 0
    first_ts_all = None; last_ts_all = None
    for m,d in toks.items():
        if d['buy_usd']<20: continue
        legs = trades.get((w,m), [])
        nlegs += len(legs)
        buys = [x for x in legs if x[1]=='buy']
        sells = [x for x in legs if x[1]=='sell']
        if buys:
            buy_sizes += [x[2] for x in buys]
            pc = pool_created_epoch(m)
            if pc: snip_secs.append(buys[0][0]-pc)
            if sells:
                s_after = [s for s in sells if s[0]>=buys[0][0]]
                if s_after: holds.append((s_after[-1][0]-buys[0][0])/60.0)
        for a,b in zip(legs, legs[1:]):
            min_gaps.append(b[0]-a[0])
        for x in legs:
            if first_ts_all is None or x[0]<first_ts_all: first_ts_all=x[0]
            if last_ts_all is None or x[0]>last_ts_all: last_ts_all=x[0]

    rcs = recon.get(w, [])
    pos_range = sorted(r['pos_in_prior90m_range'] for r in rcs if r.get('pos_in_prior90m_range') is not None)
    mfe = sorted(r['mins_from_event'] for r in rcs if r.get('mins_from_event') is not None)
    def med(a): return a[len(a)//2] if a else None

    u = union.get(w, {})
    out.append({
        'wallet': w, 'n_traded': ntr, 'n_pos': len(pos), 'n_neg': len(neg),
        'n_open': len(openb), 'net': round(net,2), 'wr': round(wr,1),
        'gross_pos': round(sum(d['covered_net_usd'] for m,d in pos),2),
        'gross_neg': round(sum(d['covered_net_usd'] for m,d in neg),2),
        'med_buy_usd': round(sorted(buy_sizes)[len(buy_sizes)//2],1) if buy_sizes else None,
        'max_buy_usd': round(max(buy_sizes),1) if buy_sizes else None,
        'min_snipe_min': round(min(snip_secs)/60.0,1) if snip_secs else None,
        'med_snipe_min': round(med(sorted(snip_secs))/60.0,1) if snip_secs else None,
        'med_hold_min': round(med(sorted(holds)),1) if holds else None,
        'min_gap_s': round(min(min_gaps),1) if min_gaps else None,
        'sub60_gap_frac': round(sum(1 for g in min_gaps if g<60)/len(min_gaps),2) if min_gaps else None,
        'nlegs': nlegs,
        'n_recon': len(rcs), 'med_pos_range': round(med(pos_range),2) if pos_range else None,
        'med_mins_from_event': round(med(mfe),0) if mfe else None,
        'sources': u.get('sources', ['io_tape']),
        'pos_syms': [tok_sym.get(m,m[:6]) for m,d in pos],
    })

json.dump(out, open(os.path.join(RD,'skeptic_wallets.json'),'w'), indent=1)

# ascii table
hdr = ('wallet12','trd','pos','neg','opn','wr%','net$','g+$','g-$','medbuy','snipe_min','hold_m','mingap_s','sub60','recon','posrng','m_evt','src')
print(('%-13s %3s %3s %3s %3s %5s %8s %8s %8s %6s %9s %6s %8s %5s %5s %6s %5s %s') % hdr)
for r in sorted(out, key=lambda r:(-r['n_pos'], -r['net'])):
    print('%-13s %3d %3d %3d %3d %5.1f %8.2f %8.2f %8.2f %6s %9s %6s %8s %5s %5d %6s %5s %s' % (
        r['wallet'][:12], r['n_traded'], r['n_pos'], r['n_neg'], r['n_open'], r['wr'], r['net'],
        r['gross_pos'], r['gross_neg'], r['med_buy_usd'], r['med_snipe_min'], r['med_hold_min'],
        r['min_gap_s'], r['sub60_gap_frac'], r['n_recon'], r['med_pos_range'], r['med_mins_from_event'],
        '+'.join(s[:4] for s in r['sources'])))
