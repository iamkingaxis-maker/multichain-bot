import json, glob, os
from datetime import datetime

RD = os.path.dirname(os.path.abspath(__file__))
def L(p): return json.load(open(os.path.join(RD,p)))

wp = L('wallet_pnl.json'); tok_sym = wp['tok_sym']; wallets = wp['wallets']
tape_index = L('tape_index.json')
runners = L('rip_runners_live.json')

# pair per token from tape_index
pair_of_token = {v['token']: p for p, v in tape_index.items()}

def ep(s):
    return datetime.fromisoformat(s).timestamp()

# For every net-positive (>=2 pos tokens) wallet in prelim, flag artifact-suspect wins:
# a pos-token win is SUSPECT if (a) wallet's covered_sell/buy ratio > 1.5 (implied gain >50%)
# AND (b) wallet's first in-tape buy happens within 30 min of tape start for that pair
# (i.e. earlier buys almost certainly invisible), OR wallet has sell_before_buy>0 on that token.
prelim = L('winners_prelim.json')['winners']
print('%-13s %6s | pos tokens: sym ratio firstbuy_after_tapestart_min suspect' % ('wallet','net$'))
for pw in prelim:
    w = pw['wallet']; rec = wallets[w]
    toks = rec['tokens']
    pos = [(m,d) for m,d in toks.items() if d['buy_usd']>=20 and d['n_sells']>0 and d['covered_net_usd']>0]
    if not pos: continue
    net = sum(d['covered_net_usd'] for m,d in toks.items() if d['buy_usd']>=20 and d['n_sells']>0)
    if net <= 0: continue
    parts = []
    clean_profit = 0.0; suspect_profit = 0.0
    for m,d in pos:
        sym = tok_sym.get(m,m[:6]).encode('ascii','replace').decode()
        ratio = (d['covered_sell_usd']/d['buy_usd']) if d['buy_usd'] else 99.0
        pair = pair_of_token.get(m)
        ti = tape_index.get(pair, {})
        fb_after = None
        if ti.get('oldest') and d.get('first_ts'):
            fb_after = (ep(d['first_ts']) - ep(ti['oldest']))/60.0
        # gain implied per dollar
        suspect = (ratio > 1.5 and (fb_after is not None and fb_after < 30)) or d.get('sell_before_buy_usd',0) > 0.5*d['buy_usd']
        if suspect: suspect_profit += d['covered_net_usd']
        else: clean_profit += d['covered_net_usd']
        parts.append('%s r=%.2f fb+%.0fm %s$%.0f' % (sym, ratio, fb_after if fb_after is not None else -1,
                     'SUS ' if suspect else 'ok ', d['covered_net_usd']))
    print('%-13s %7.2f | clean=$%.2f suspect=$%.2f || %s' % (w[:12], net, clean_profit, suspect_profit, ' ; '.join(parts)))
