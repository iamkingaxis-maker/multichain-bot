#!/usr/bin/env python3
"""T4 reachable P&L. Authoritative per-sell pnl_pct (book B). A=stale entry_mid_price.
A->B is a pure ENTRY transform: A_ret = (1+B_ret)*drift - 1, drift=entry_price/entry_mid_price.
Token-level (distinct address) aggregation."""
import json, statistics
from collections import defaultdict

d = json.load(open('_full_trades.json'))
buys=[r for r in d if r.get('type')=='buy']
sells=[r for r in d if r.get('type')=='sell']

# address -> list of (entry_price, entry_mid_price, amount_usd)
bybuy=defaultdict(list)
for b in buys:
    ep=b.get('entry_price'); mp=b.get('entry_mid_price')
    if ep and mp and ep>0 and mp>0:
        bybuy[b.get('address')].append((ep,mp,b.get('amount_usd') or 0.0))

def match_buy(addr, ep):
    cands=bybuy.get(addr)
    if not cands: return None
    return min(cands, key=lambda c: abs(c[0]-ep)/ep if ep else abs(c[0]-c[0]))

rows=[]
unmatched=0
for s in sells:
    pp=s.get('pnl_pct'); ep=s.get('entry_price'); addr=s.get('address')
    if pp is None or ep is None or ep<=0: continue
    if pp>1000 or pp<-100: continue  # glitch guard
    mb=match_buy(addr,ep)
    if mb is None: unmatched+=1; continue
    bep,bmp,amt=mb
    # relative mismatch guard: matched buy entry should be ~= sell entry
    if bep and abs(bep-ep)/ep>0.02:  # >2% off -> not the same lot
        unmatched+=1; continue
    drift=bep/bmp  # entry_price/entry_mid_price (>1 means we filled above stale)
    B_ret=pp/100.0
    A_ret=(1.0+B_ret)*drift-1.0
    fr=s.get('sell_fraction'); fr=float(fr) if fr is not None else 1.0
    notional=amt*fr
    rows.append({'addr':addr,'token':s.get('token'),'bot_id':s.get('bot_id'),
                 'A':A_ret*100,'B':B_ret*100,'drift':(drift-1)*100,
                 'usdA':notional*A_ret,'usdB':notional*B_ret,'notional':notional})

print('matched sell legs:',len(rows),' unmatched:',unmatched)

def describe(label, pcts, usds):
    wins=sum(1 for p in pcts if p>0)
    print('%-10s n=%5d mean=%7.2f%% median=%7.2f%% WR=%5.1f%% total=$%9.0f'%(
        label,len(pcts),statistics.mean(pcts),statistics.median(pcts),100*wins/len(pcts),sum(usds)))

print('\n=== RAW SELL-LEG LEVEL ===')
describe('A_stale',[r['A'] for r in rows],[r['usdA'] for r in rows])
describe('B_current',[r['B'] for r in rows],[r['usdB'] for r in rows])

# token-level: mean pct across that address's legs; sum usd
tok=defaultdict(lambda:{'A':[],'B':[],'uA':0.0,'uB':0.0})
for r in rows:
    t=tok[r['addr']]; t['A'].append(r['A']); t['B'].append(r['B']); t['uA']+=r['usdA']; t['uB']+=r['usdB']
print('\n=== TOKEN-LEVEL (distinct address) ===')
A=[statistics.mean(v['A']) for v in tok.values()]
B=[statistics.mean(v['B']) for v in tok.values()]
uA=[v['uA'] for v in tok.values()]; uB=[v['uB'] for v in tok.values()]
describe('A_stale',A,uA)
describe('B_current',B,uB)
print('distinct tokens:',len(tok))

drift=[r['drift'] for r in rows]
print('\nA->B drift median=%.2f%% mean=%.2f%%'%(statistics.median(drift),statistics.mean(drift)))
flip=sum(1 for r in rows if r['A']>0 and r['B']<=0)
print('legs where A>0 but B<=0 (edge erased by drift): %d (%.1f%%)'%(flip,100*flip/len(rows)))
