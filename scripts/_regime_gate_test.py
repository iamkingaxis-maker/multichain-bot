"""Test concrete entry GATES in the PUMP window vs PRE-PUMP. Numeric thresholds."""
from __future__ import annotations
import pickle, statistics as st
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
with open(ROOT / '_regime_enriched.pkl', 'rb') as f:
    D = pickle.load(f)
PRE, PUMP = D['PRE'], D['PUMP']


def tok_dedup(rows):
    by = defaultdict(list)
    for r in rows:
        by[r['addr']].append(r)
    out = []
    for a, rs in by.items():
        pnl = st.median([r['pnl'] for r in rs])
        fm = {}
        keys = set()
        for r in rs:
            keys.update(r['f'].keys())
        for k in keys:
            vs = [r['f'][k] for r in rs if r['f'].get(k) is not None]
            if vs:
                fm[k] = st.median(vs)
        out.append({'addr': a, 'tok': rs[0]['tok'], 'pnl': pnl, 'win': pnl > 0, 'f': fm,
                    'pnl_sum': sum(r['pnl'] for r in rs), 'n': len(rs)})
    return out


def wr_dollar(toks, pred, label):
    sel = [t for t in toks if pred(t)]
    rej = [t for t in toks if not t]  # placeholder
    if not sel:
        print(f'  {label:42} n=0')
        return
    wr = 100 * sum(1 for t in sel if t['win']) / len(sel)
    mp = st.median([t['pnl'] for t in sel])
    meanp = st.mean([t['pnl'] for t in sel])
    print(f'  {label:42} n={len(sel):2}  WR={wr:3.0f}%  med={mp:+6.2f}  mean={meanp:+6.2f}')


pre_t = tok_dedup(PRE)
pump_t = tok_dedup(PUMP)


def have(t, *ks):
    return all(t['f'].get(k) is not None for k in ks)


print('TOKEN-DEDUPED GATE TESTS  (WR + median token pnl)')
for label, toks in [('PRE-PUMP', pre_t), ('PUMP', pump_t)]:
    print(f'\n##### {label}  (baseline: n={len(toks)}, '
          f'WR={100*sum(1 for t in toks if t["win"])/len(toks):.0f}%, '
          f'med={st.median([t["pnl"] for t in toks]):+.2f}) #####')

    # Old "green-bot" doctrine: DEEP DIP + at/near lows
    wr_dollar(toks, lambda t: have(t, 'dip90') and t['f']['dip90'] <= -20,
              'DEEP-DIP  dip90<=-20%')
    wr_dollar(toks, lambda t: have(t, 'dip90') and t['f']['dip90'] <= -25,
              'DEEPER-DIP dip90<=-25%')
    wr_dollar(toks, lambda t: have(t, 'pos90') and t['f']['pos90'] <= 0.25,
              'AT-LOWS    pos90<=0.25')
    wr_dollar(toks, lambda t: have(t, 'dip90', 'pos90') and t['f']['dip90'] <= -20 and t['f']['pos90'] <= 0.3,
              'DEEP-DIP+AT-LOWS (old doctrine)')

    # New "pump/momentum" doctrine candidates
    wr_dollar(toks, lambda t: have(t, 'mom30') and t['f']['mom30'] >= -2,
              'MOMENTUM30 mom30>=-2%')
    wr_dollar(toks, lambda t: have(t, 'mom30') and t['f']['mom30'] >= 0,
              'MOMENTUM30 mom30>=0%')
    wr_dollar(toks, lambda t: have(t, 'mom60') and t['f']['mom60'] >= -2,
              'MOMENTUM60 mom60>=-2%')
    wr_dollar(toks, lambda t: have(t, 'mom60') and t['f']['mom60'] >= 0,
              'MOMENTUM60 mom60>=0%')
    wr_dollar(toks, lambda t: have(t, 'pos90') and t['f']['pos90'] >= 0.35,
              'UPPER-RANGE pos90>=0.35')
    wr_dollar(toks, lambda t: have(t, 'pos90') and t['f']['pos90'] >= 0.5,
              'UPPER-RANGE pos90>=0.50')
    wr_dollar(toks, lambda t: have(t, 'mom60', 'pos90') and t['f']['mom60'] >= -2 and t['f']['pos90'] >= 0.35,
              'MOM60>=-2 & UPPER-RANGE pos90>=0.35')
    wr_dollar(toks, lambda t: have(t, 'mom30', 'mom60') and t['f']['mom30'] >= -3 and t['f']['mom60'] >= -3,
              'BOTH-MOM mom30>=-3 & mom60>=-3')


# Direct contrast table on the two regimes for the headline gate
print('\n' + '=' * 80)
print('HEADLINE CONTRAST: deep-dip-at-lows  vs  momentum-upper-range')
print('=' * 80)


def cohort(toks, pred):
    sel = [t for t in toks if pred(t)]
    if not sel:
        return (0, None, None)
    return (len(sel), 100 * sum(1 for t in sel if t['win']) / len(sel),
            st.median([t['pnl'] for t in sel]))


def show(name, predA, predB):
    for lab, toks in [('PRE', pre_t), ('PUMP', pump_t)]:
        nA, wrA, mA = cohort(toks, predA)
        nB, wrB, mB = cohort(toks, predB)
        sA = f'n={nA} WR={wrA:.0f}% med={mA:+.2f}' if nA else 'n=0'
        sB = f'n={nB} WR={wrB:.0f}% med={mB:+.2f}' if nB else 'n=0'
        print(f'  [{lab:4}] DEEP-DIP-LOWS: {sA:30}   MOM-UPPER: {sB}')


show('x',
     lambda t: have(t, 'dip90', 'pos90') and t['f']['dip90'] <= -20 and t['f']['pos90'] <= 0.3,
     lambda t: have(t, 'mom60', 'pos90') and t['f']['mom60'] >= -2 and t['f']['pos90'] >= 0.35)
