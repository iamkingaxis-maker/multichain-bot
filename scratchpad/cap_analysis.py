"""Capture-leak analysis on cache sell legs (07-11..07-13) + shadow attribution."""
import json, statistics as st
from collections import defaultdict, Counter

rows = [json.loads(l) for l in open('scratchpad/_cap_sells.jsonl', encoding='utf-8')]
YOUNG = {'badday_young_absorb', 'badday_young_rt_paper', 'badday_young_rt',
         'badday_young_pump_dip_ab', 'badday_young_moonbag_ab',
         'badday_young_adaptsize_ab', 'badday_young_vsnap_ab'}


def rlabel(r):
    return r['reason'].split(' ')[0].split(':')[0]


def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else float('nan')


# ---- reconstruct positions ----
pos = defaultdict(list)
for r in rows:
    if r['bot_id'] not in YOUNG:
        continue
    key = (r['bot_id'], r['address'], round(r.get('peak_pnl_pct') or 0.0, 4))
    pos[key].append(r)

positions = []
for key, legs in pos.items():
    legs = sorted(legs, key=lambda x: x['time'])
    peak = key[2]
    reasons = [rlabel(l) for l in legs]
    if len(legs) == 1:
        fr = [1.0]
    else:
        has_tp1 = any(rl == 'TP1' for rl in reasons)
        if has_tp1:
            rest = len(legs) - 1
            fr = [0.75 if rl == 'TP1' else 0.25 / rest for rl in reasons]
        else:
            fr = [1.0 / len(legs)] * len(legs)
    blended = sum(f * l['pnl_pct'] for f, l in zip(fr, legs))
    positions.append(dict(
        bot=key[0], addr=key[1], token=legs[0]['token'], time=legs[0]['time'],
        peak=peak, blended=blended, reasons=reasons,
        mae=legs[0].get('max_drawdown_pct'),
        runner_score=legs[0].get('runner_score'),
        hold=max(l['hold_secs'] for l in legs), legs=legs))

print('young-lane positions reconstructed:', len(positions))


def cap_eff(p):
    return p['blended'] / p['peak'] if p['peak'] > 0 else None


win = [p for p in positions if p['peak'] >= 6]
print('positions reaching +6 (winners):', len(win),
      '(%.0f%%)' % (100 * len(win) / len(positions)))
ce = [cap_eff(p) for p in win]
leak = [p['peak'] - p['blended'] for p in win]
print('capture eff (blended/peak) winners: med=%.2f mean=%.2f' % (med(ce), st.mean(ce)))
print('MFE left on table (peak-blended): med=%+.1fpp mean=%+.1fpp' % (med(leak), st.mean(leak)))

print('\ncapture by MFE bucket (winners):')
print('%-14s %4s %8s %9s %8s %8s' % ('peak-bucket', 'n', 'medPeak', 'medBlend', 'capEff', 'leakPP'))
for lo, hi in [(6, 12), (12, 18), (18, 30), (30, 1e9)]:
    b = [p for p in win if lo <= p['peak'] < hi]
    if b:
        print('%-14s %4d %8.1f %+9.1f %8.2f %+8.1f' % (
            '[%g,%g)' % (lo, hi), len(b), med([p['peak'] for p in b]),
            med([p['blended'] for p in b]), med([cap_eff(p) for p in b]),
            med([p['peak'] - p['blended'] for p in b])))

# ---- where does the winner exit happen? reason of the LAST/dominant leg ----
print('\nwinner exit reason mix (positions reaching +6, by 0.75-weight TP1 vs remainder):')
print(Counter(tuple(sorted(set(p['reasons']))) for p in win).most_common(12))

# ---- absorb vs siblings capture on winners ----
print('\nper-bot winner capture (peak>=6):')
print('%-30s %4s %5s %8s %8s %7s' % ('bot', 'nWin', 'win%', 'medPeak', 'medBlend', 'capEff'))
for bot in sorted(YOUNG):
    bp = [p for p in positions if p['bot'] == bot]
    bw = [p for p in bp if p['peak'] >= 6]
    if not bp:
        continue
    print('%-30s %4d %5.0f %8.1f %+8.1f %7.2f' % (
        bot, len(bw), 100 * len(bw) / len(bp) if bp else 0,
        med([p['peak'] for p in bw]) if bw else float('nan'),
        med([p['blended'] for p in bw]) if bw else float('nan'),
        med([cap_eff(p) for p in bw]) if bw else float('nan')))
