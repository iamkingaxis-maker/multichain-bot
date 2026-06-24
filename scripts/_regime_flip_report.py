"""Report: which entry features FLIP winner-vs-loser sign between PRE-PUMP and PUMP."""
from __future__ import annotations
import pickle, statistics as st
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
with open(ROOT / '_regime_enriched.pkl', 'rb') as f:
    D = pickle.load(f)
PRE, PUMP = D['PRE'], D['PUMP']

FEATS = ['dip90', 'dip30', 'mom5', 'mom15', 'mom30', 'mom60',
         'pos90', 'pos30', 'vol15', 'green_run', 'vol_burst',
         'mcap', 'age', 'volh1']


def tok_dedup(rows):
    """One row per token: median pnl + median of each feature."""
    by = defaultdict(list)
    for r in rows:
        by[r['addr']].append(r)
    out = []
    for a, rs in by.items():
        pnl = st.median([r['pnl'] for r in rs])
        fm = {}
        for k in FEATS:
            vs = [r['f'][k] for r in rs if r['f'].get(k) is not None]
            if vs:
                fm[k] = st.median(vs)
        out.append({'addr': a, 'tok': rs[0]['tok'], 'pnl': pnl, 'win': pnl > 0, 'f': fm})
    return out


def med(xs):
    return st.median(xs) if xs else None


def win_loss_meds(toks, feat):
    w = [t['f'][feat] for t in toks if t['win'] and feat in t['f']]
    l = [t['f'][feat] for t in toks if (not t['win']) and feat in t['f']]
    return med(w), med(l), len(w), len(l)


def report(label, rows):
    toks = tok_dedup(rows)
    nw = sum(1 for t in toks if t['win'])
    nl = len(toks) - nw
    print(f'\n=== {label}: {len(toks)} tokens ({nw}W/{nl}L), trade-WR over rows = '
          f'{100*sum(1 for r in rows if r["pnl"]>0)/len(rows):.0f}% ===')
    print(f'    median token pnl = {med([t["pnl"] for t in toks]):+.2f}')
    return toks


pre_t = report('PRE-PUMP (06:00-14:00)', PRE)
pump_t = report('PUMP (>=19:00)', PUMP)

print('\n' + '=' * 96)
print('WINNER-vs-LOSER MEDIANS per window, and the FLIP')
print('=' * 96)
print(f'{"feature":11} | {"PRE win":>9} {"PRE loss":>9} {"PREw-l":>8} | '
      f'{"PUMP win":>9} {"PUMP loss":>9} {"PUMPw-l":>8} | flip?')
print('-' * 96)
flips = []
for k in FEATS:
    pw, pl, npw, npl = win_loss_meds(pre_t, k)
    uw, ul, nuw, nul = win_loss_meds(pump_t, k)
    if pw is None or pl is None or uw is None or ul is None:
        continue
    pre_sep = pw - pl
    pump_sep = uw - ul
    # flip = winner>loser direction reverses, and both sides have >=4 each
    enough = min(npw, npl, nuw, nul) >= 4
    flip = ''
    if enough and pre_sep != 0 and pump_sep != 0 and (pre_sep > 0) != (pump_sep > 0):
        flip = 'FLIP'
    elif enough and (pre_sep > 0) == (pump_sep > 0) and abs(pump_sep) > abs(pre_sep) * 1.5:
        flip = 'amplified'
    print(f'{k:11} | {pw:9.3g} {pl:9.3g} {pre_sep:+8.3g} | '
          f'{uw:9.3g} {ul:9.3g} {pump_sep:+8.3g} | {flip}'
          + ('' if enough else '  (thin n<4/side)'))
    if flip == 'FLIP':
        flips.append((k, pre_sep, pump_sep, npw, npl, nuw, nul))

print('\n' + '=' * 96)
print('OVERALL FEATURE LEVEL SHIFT (all tokens, not just winners): PRE -> PUMP')
print('=' * 96)
print(f'{"feature":11} | {"PRE all":>10} {"PUMP all":>10} {"shift":>10} | {"PRE n":>6} {"PUMP n":>7}')
print('-' * 70)
for k in FEATS:
    pa = [t['f'][k] for t in pre_t if k in t['f']]
    ua = [t['f'][k] for t in pump_t if k in t['f']]
    if not pa or not ua:
        continue
    mp, mu = med(pa), med(ua)
    print(f'{k:11} | {mp:10.4g} {mu:10.4g} {mu-mp:+10.4g} | {len(pa):6} {len(ua):7}')

# Winner-only profile shift: what do PUMP winners look like vs PRE winners?
print('\n' + '=' * 96)
print('WINNER PROFILE SHIFT: median feature among WINNERS, PRE -> PUMP')
print('=' * 96)
print(f'{"feature":11} | {"PRE winners":>12} {"PUMP winners":>13} {"shift":>10}')
print('-' * 60)
for k in FEATS:
    pw = [t['f'][k] for t in pre_t if t['win'] and k in t['f']]
    uw = [t['f'][k] for t in pump_t if t['win'] and k in t['f']]
    if not pw or not uw:
        continue
    print(f'{k:11} | {med(pw):12.4g} {med(uw):13.4g} {med(uw)-med(pw):+10.4g}')

print('\nFLIPPED FEATURES (winner-loser sign reversed, >=4/side both windows):')
for k, ps, us, a, b, c, d in flips:
    direction_pre = 'winners HIGHER' if ps > 0 else 'winners LOWER'
    direction_pump = 'winners HIGHER' if us > 0 else 'winners LOWER'
    print(f'  {k}: PRE {direction_pre} ({ps:+.3g}) -> PUMP {direction_pump} ({us:+.3g}) '
          f'[PRE {a}W/{b}L, PUMP {c}W/{d}L]')
