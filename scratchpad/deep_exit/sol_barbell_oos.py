"""SOL deep cohort: 4-half OOS on the depth-band expectancy + a barbell exit
ESTIMATE from observed MFE/MAE.

HONEST LIMIT (restated): observed MFE is truncated by the live exit, so the
barbell RUNNER leg here is a LOWER bound (a runner could ride past where the
live ladder sold). The FAST leg and the depth-band expectancy split are reliable.
"""
import json, statistics as st
from collections import defaultdict

FILES = ['scratchpad/_full_trades.json', 'scratchpad/sol_selection/_trades_full.json']

def num(x):
    try: return None if x is None else float(x)
    except Exception: return None

recs = {}
for fp in FILES:
    try: data = json.load(open(fp))
    except Exception: continue
    for r in data:
        if 'young' not in (r.get('bot_id') or ''): continue
        key = (r.get('bot_id'), r.get('token'), r.get('address'), r.get('type'),
               r.get('time'), round(float(r.get('entry_price') or 0), 12),
               round(float(r.get('exit_price') or 0), 12))
        recs[key] = r
recs = list(recs.values())
buys = [r for r in recs if r.get('type') == 'buy']
sells = [r for r in recs if r.get('type') == 'sell']
bidx = defaultdict(list)
for r in buys: bidx[(r.get('bot_id'), r.get('address'))].append(r)
for lst in bidx.values(): lst.sort(key=lambda r: r.get('time') or '')

positions = defaultdict(list); buy_of = {}
for s in sells:
    cands = bidx.get((s.get('bot_id'), s.get('address')), [])
    ep = num(s.get('entry_price')); stime = s.get('time') or ''
    best = None
    for b in cands:
        if (b.get('time') or '') > stime: continue
        bp = num(b.get('entry_price'))
        if ep and bp and abs(bp-ep)/ep < 0.02: best = b
    if best is None:
        for b in cands:
            bp = num(b.get('entry_price'))
            if ep and bp and abs(bp-ep)/ep < 0.02: best = b
    if best is None: continue
    buy_of[id(best)] = best; positions[id(best)].append(s)

POS = []
for bid, legs in positions.items():
    b = buy_of[bid]; em = b.get('entry_meta') or {}
    pc_h1 = num(em.get('pc_h1'))
    good = [s for s in legs if not (num(s.get('pnl_pct')) and num(s.get('hold_secs'))
            and num(s.get('pnl_pct')) > 0 and num(s.get('hold_secs')) < 10)]
    if not good: continue
    fracs = [num(s.get('sell_fraction')) or 0 for s in good]
    rets = [num(s.get('pnl_pct')) for s in good]
    peaks = [num(s.get('peak_pnl_pct')) for s in good if num(s.get('peak_pnl_pct')) is not None]
    maes = [num(s.get('mae_pct')) for s in good if num(s.get('mae_pct')) is not None]
    fsum = sum(fracs)
    if fsum <= 0: fracs = [1.0/len(good)]*len(good); fsum = 1.0
    realized = sum(f*r for f, r in zip(fracs, rets) if r is not None)/fsum
    POS.append({'address': b.get('address'), 'pc_h1': pc_h1, 'realized': realized,
                'mfe': max(peaks) if peaks else None, 'mae': min(maes) if maes else None,
                'day': (b.get('time') or '')[:10]})

W = [p for p in POS if p['realized'] is not None and p['pc_h1'] is not None]

def half_tags(day):
    try: dom = int(day[8:10])
    except Exception: return []
    return [('W1' if day <= '2026-07-06' else 'W2'), ('odd' if dom%2 else 'even')]

# ---- 4-half OOS on depth-band mean expectancy ----
print("=== depth-band mean realized, per half (OOS) ===")
bands = [(-1e9, -60, '<=-60'), (-60, -45, '-45..-60'), (-45, -30, '-30..-45'), (-30, 1e9, '>-30')]
print(f"{'band':10s} {'ALL':>8s} {'W1':>7s} {'W2':>7s} {'odd':>7s} {'even':>7s} {'n':>4s}")
for lo, hi, lbl in bands:
    g = [p for p in W if lo < p['pc_h1'] <= hi]
    row = f"{lbl:10s} {st.mean([p['realized'] for p in g]):+8.2f}"
    for tag in ['W1', 'W2', 'odd', 'even']:
        sub = [p['realized'] for p in g if tag in half_tags(p['day'])]
        row += f" {(st.mean(sub) if sub else float('nan')):+7.2f}"
    row += f" {len(g):4d}"
    print(row)

# ---- barbell ESTIMATE per depth band ----
# fast leg: sell f at +T (needs MFE>=T). runner (1-f): rides to observed MFE,
# trails by Wtr, breakeven floor 0. If MFE<T -> no TP, whole pos = live realized.
def barbell(g, T=5.0, f=0.8, Wtr=15.0, S=-15.0):
    out = []
    for p in g:
        M = p['mfe']; m = p['mae']; live = p['realized']
        if M is None: out.append(live); continue
        if m is not None and m <= S and M < T:
            out.append(S); continue
        if M >= T:
            runner = max(0.0, min(M, M - Wtr))   # breakeven floor, lower-bound
            out.append(f*min(M, T) + (1-f)*runner)
        else:
            out.append(live)
    return out

def fast(g, T=4.0, S=-15.0):
    out = []
    for p in g:
        M = p['mfe']; m = p['mae']; live = p['realized']
        if M is None: out.append(live); continue
        if M >= T and not (m is not None and m <= S and M < T): out.append(min(M, T))
        elif m is not None and m <= S: out.append(S)
        else: out.append(live)
    return out

def stats(o):
    return (st.median(o), st.mean(o), 100*sum(1 for x in o if x > 0)/len(o))

print("\n=== per depth band: LIVE vs fast@+4 vs barbell(0.8@+5, runner trail15 floor0) ===")
print(f"{'band':10s} {'exit':28s} {'med':>7s} {'mean':>7s} {'wr%':>5s}")
for lo, hi, lbl in bands:
    g = [p for p in W if lo < p['pc_h1'] <= hi]
    if len(g) < 5: continue
    for label, o in [('LIVE', [p['realized'] for p in g]),
                     ('fast@+4', fast(g)),
                     ('barbell 0.8@+5 run15', barbell(g))]:
        md, mn, wr = stats(o)
        print(f"{lbl:10s} {label:28s} {md:+7.2f} {mn:+7.2f} {wr:5.0f}")
    print()
