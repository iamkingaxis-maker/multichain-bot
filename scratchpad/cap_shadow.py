"""Attribute the capture leak using shadow fields + peak timing (cache legs)."""
import json, statistics as st
from collections import defaultdict

rows = [json.loads(l) for l in open('scratchpad/_cap_sells.jsonl', encoding='utf-8')]
YOUNG = {'badday_young_absorb', 'badday_young_rt_paper', 'badday_young_rt',
         'badday_young_pump_dip_ab', 'badday_young_moonbag_ab',
         'badday_young_adaptsize_ab', 'badday_young_vsnap_ab'}
yr = [r for r in rows if r['bot_id'] in YOUNG]


def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else float('nan')


def rlabel(r):
    return r['reason'].split(' ')[0].split(':')[0]


# --- peak timing: when does MFE occur relative to hold? ---
tp1_legs = [r for r in yr if rlabel(r) == 'TP1']
print('TP1 legs:', len(tp1_legs))
# peak_pnl_at_secs mostly None in cache; check availability
have_pas = [r for r in yr if r.get('peak_pnl_at_secs') is not None]
print('rows with peak_pnl_at_secs:', len(have_pas), '/', len(yr))

# --- TP1 fast-follow shadow: did a later/earlier TP1 capture more? ---
ff = [r for r in yr if r.get('tp1_ff_shadow_pnl') is not None]
print('\ntp1_ff_shadow rows:', len(ff))
if ff:
    print('  live TP1 leg pnl med=%.1f  vs tp1_ff_shadow_pnl med=%.1f  (delta med=%+.1f)'
          % (med([r['pnl_pct'] for r in ff]), med([r['tp1_ff_shadow_pnl'] for r in ff]),
             med([r['tp1_ff_shadow_pnl'] - r['pnl_pct'] for r in ff])))

# --- trail reprice shadow: would a wider/repriced trail capture more on runners? ---
tr = [r for r in yr if r.get('trail_reprice_shadow_pnl') is not None]
print('\ntrail_reprice_shadow rows:', len(tr))
if tr:
    print('  live pnl med=%.1f  trail_reprice_shadow_pnl med=%.1f  (delta med=%+.1f, mean=%+.1f)'
          % (med([r['pnl_pct'] for r in tr]), med([r['trail_reprice_shadow_pnl'] for r in tr]),
             med([r['trail_reprice_shadow_pnl'] - r['pnl_pct'] for r in tr]),
             st.mean([r['trail_reprice_shadow_pnl'] - r['pnl_pct'] for r in tr])))
    # on runners (peak>=12)
    trh = [r for r in tr if (r.get('peak_pnl_pct') or 0) >= 12]
    if trh:
        print('  [peak>=12] n=%d live med=%.1f reprice med=%.1f delta med=%+.1f mean=%+.1f'
              % (len(trh), med([r['pnl_pct'] for r in trh]),
                 med([r['trail_reprice_shadow_pnl'] for r in trh]),
                 med([r['trail_reprice_shadow_pnl'] - r['pnl_pct'] for r in trh]),
                 st.mean([r['trail_reprice_shadow_pnl'] - r['pnl_pct'] for r in trh])))

# --- runner_score: is capture better when runner well-armed? ---
print('\nrunner_score vs capture (TP1 legs, peak>=6):')
rs = [r for r in tp1_legs if r.get('runner_score') is not None and (r.get('peak_pnl_pct') or 0) >= 6]
for lo, hi in [(0, 0.4), (0.4, 0.7), (0.7, 1.01)]:
    b = [r for r in rs if lo <= r['runner_score'] < hi]
    if b:
        print('  rscore[%.1f,%.1f): n=%d medPeak=%.1f med_tp1_pnl=%.1f  peak-tp1=%+.1f'
              % (lo, hi, len(b), med([r['peak_pnl_pct'] for r in b]),
                 med([r['pnl_pct'] for r in b]),
                 med([r['peak_pnl_pct'] - r['pnl_pct'] for r in b])))

# --- timestop45 / giveback shadows: are we exiting via time/giveback and leaving MFE? ---
for fld in ('timestop45_fired', 'giveback_shadow_fired', 'never_runner_fired'):
    fired = [r for r in yr if r.get(fld)]
    if fired:
        pk = [r for r in fired if (r.get('peak_pnl_pct') or 0) >= 6]
        print('\n%s fired: %d (of which peak>=6: %d)' % (fld, len(fired), len(pk)))
        if pk:
            print('   peak>=6 fires: medPeak=%.1f med_pnl=%.1f leak=%+.1f'
                  % (med([r['peak_pnl_pct'] for r in pk]), med([r['pnl_pct'] for r in pk]),
                     med([r['peak_pnl_pct'] - r['pnl_pct'] for r in pk])))
