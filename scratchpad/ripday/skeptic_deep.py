import json, glob, os
from datetime import datetime, timezone

RD = os.path.dirname(os.path.abspath(__file__))
def L(p): return json.load(open(os.path.join(RD,p)))

wp = L('wallet_pnl.json'); tok_sym = wp['tok_sym']; wallets = wp['wallets']
meta = L('token_meta.json')
runners = L('rip_runners_live.json')
tape_index = L('tape_index.json')

# ---------- luck base rate ----------
n3 = n3pos = n2 = n2pos = 0
big3 = []
for w, rec in wallets.items():
    toks = rec['tokens']
    closed = [(m,d) for m,d in toks.items() if d['buy_usd']>=20 and d['n_sells']>0]
    pos = [x for x in closed if x[1]['covered_net_usd']>0]
    net = sum(d['covered_net_usd'] for m,d in closed)
    if len(closed)>=3:
        n3 += 1
        if len(pos)>=3 and net>0:
            n3pos += 1
            big3.append((w, len(pos), len(closed), round(net,2)))
    if len(closed)>=2:
        n2 += 1
        if len(pos)>=2 and net>0: n2pos += 1
print('wallets with >=3 closed traded tokens: %d ; of those >=3 pos AND net>0: %d (%.1f%%)' % (n3, n3pos, 100.0*n3pos/max(n3,1)))
print('wallets with >=2 closed traded tokens: %d ; of those >=2 pos AND net>0: %d (%.1f%%)' % (n2, n2pos, 100.0*n2pos/max(n2,1)))
print('strict-bar wallets:', sorted(big3, key=lambda x:-x[3]))
print()

# ---------- deep dive ----------
TARGETS = ['kEFiAX3jo5NmemysQov342TZ9mGh6yp92GDRjhA8XDf',
           'DJocqRPK2uKWvmR5WnWcd7m8fDw6az1L54R4UuH3GrGN',
           'DF8tRgFkt1JSuqqtVmG2maiEY92mfFWBHNpMeRBK4fEo',
           '4MB2yiq54PHkJ11YPoZGYgVzew9zFRRms41PAFoXaevg',
           '7JCe3GHwkEr3feHgtLXnmuJ1yB3A7coSeyynxTBgdG8k',
           'J1sfMsbxGNXDPMUPXyGs5D6oCEe7fSYgdPMRyVzZuZUW',
           'BGzLYcFcUZkW5GPZZAYK4Jxyf1W7aigyHQbvmKsQeeuq']

# tape legs by (wallet, token)
legs = {}
pair_of_token = {}
for f in glob.glob(os.path.join(RD,'tape_*.jsonl')):
    for line in open(f, encoding='utf-8'):
        t = json.loads(line)
        if t['maker'] not in TARGETS: continue
        legs.setdefault((t['maker'], t['token']), []).append((t['ts'], t['kind'], t['volume_usd']))
        pair_of_token[t['token']] = t['pair']
for k in legs: legs[k].sort()

# ohlc lookup
ohlc = {}
for f in glob.glob(os.path.join(RD,'ohlc_*.json')):
    try:
        d = json.load(open(f))
        if d.get('bars'): ohlc[d['token']] = d
    except Exception: pass

def px_at(token, ep):
    d = ohlc.get(token)
    if not d: return None
    bars = d['bars']
    best = None
    for b in bars:
        if b[0] <= ep: best = b
        else: break
    return best[4] if best else None

for w in TARGETS:
    rec = wallets.get(w)
    if not rec: continue
    print('='*100)
    print('WALLET', w)
    for m, d in sorted(rec['tokens'].items(), key=lambda kv: kv[1].get('first_ts') or ''):
        if d['buy_usd'] < 5 and d['sell_usd'] < 5: continue
        sym = tok_sym.get(m, m[:8]).encode('ascii','replace').decode()
        ev = (runners.get(m) or {}).get('ts')
        pair = pair_of_token.get(m) or (runners.get(m) or {}).get('pair')
        pc = (meta.get(pair) or {}).get('pool_created_at','?')
        ti = tape_index.get(pair, {})
        print(' TOKEN %-12s buy=$%-8.2f sell=$%-8.2f cov_net=$%-8.2f first_kind=%s sell_b4_buy=$%.2f nb=%d ns=%d' % (
            sym, d['buy_usd'], d['sell_usd'], d['covered_net_usd'], d['first_kind'], d.get('sell_before_buy_usd',0), d['n_buys'], d['n_sells']))
        print('   pool_created=%s tape_span=%s..%s event=%s' % (pc, ti.get('oldest','?'), ti.get('newest','?'),
              datetime.fromtimestamp(ev, tz=timezone.utc).strftime('%m-%dT%H:%M') if ev else '?'))
        for ts, kind, usd in legs.get((w,m), []):
            ep = datetime.fromisoformat(ts).timestamp()
            px = px_at(m, ep)
            evs = '' if not ev else ' evt%+.0fm' % ((ep-ev)/60.0)
            print('    %s %-4s $%8.2f px=%s%s' % (ts[5:19], kind, usd, ('%.3e'%px) if px else 'n/a', evs))
