"""SOL deep-cohort exit analysis (2026-07-12).

Reconstructs POSITIONS (merge TP1/TP2/trail legs) from the young-lane full trades,
computes position-level MFE(peak)/MAE/realized, and characterizes the DEEP cohort
(entry pc_h1 <= -45) giveback vs the rest.

HONEST LIMIT: peak_pnl_pct is the max favorable excursion observed UP TO the live
exit -> it is TRUNCATED for grinders the current ladder sold at +6. So:
  - FAST-harvest variants (TP target <= current +6 TP1) are RELIABLY testable:
    if position MFE >= T the +T print happened, so a sell-100%-at-T would have fired.
  - PATIENT / higher-TP variants are NOT testable here (unobservable beyond the live
    exit) -> deferred to the RH real-tape sweep.
Ordering: mae_at_secs vs the peak timing lets us respect stop-before-TP where knowable.
"""
import json, statistics as st
from collections import defaultdict

FILES = ['scratchpad/_full_trades.json', 'scratchpad/sol_selection/_trades_full.json']

def num(x):
    try:
        return None if x is None else float(x)
    except Exception:
        return None

recs = {}
for fp in FILES:
    try:
        data = json.load(open(fp))
    except Exception:
        continue
    for r in data:
        b = r.get('bot_id') or ''
        if 'young' not in b:
            continue
        key = (b, r.get('token'), r.get('address'), r.get('type'), r.get('time'),
               round(float(r.get('entry_price') or 0), 12),
               round(float(r.get('exit_price') or 0), 12))
        recs[key] = r
recs = list(recs.values())
buys = [r for r in recs if r.get('type') == 'buy']
sells = [r for r in recs if r.get('type') == 'sell']

bidx = defaultdict(list)
for r in buys:
    bidx[(r.get('bot_id'), r.get('address'))].append(r)
for lst in bidx.values():
    lst.sort(key=lambda r: r.get('time') or '')

# match each sell to its buy; group legs by matched buy (=position)
positions = defaultdict(list)   # buy-id -> list of sell legs
buy_of = {}
for s in sells:
    key = (s.get('bot_id'), s.get('address'))
    cands = bidx.get(key, [])
    ep = num(s.get('entry_price')); stime = s.get('time') or ''
    best = None
    for b in cands:
        if (b.get('time') or '') > stime:
            continue
        bp = num(b.get('entry_price'))
        if ep and bp and abs(bp - ep) / ep < 0.02:
            best = b
    if best is None:
        for b in cands:
            bp = num(b.get('entry_price'))
            if ep and bp and abs(bp - ep) / ep < 0.02:
                best = b
    if best is None:
        continue
    bid = id(best)
    buy_of[bid] = best
    positions[bid].append(s)

# build position records
POS = []
for bid, legs in positions.items():
    b = buy_of[bid]
    em = b.get('entry_meta') or {}
    pc_h1 = num(em.get('pc_h1'))
    # SCRUB: drop legs ret>0 & hold<10s (phantom), then require remaining legs
    good = []
    for s in legs:
        ret = num(s.get('pnl_pct')); hold = num(s.get('hold_secs'))
        if ret is not None and hold is not None and ret > 0 and hold < 10:
            continue
        good.append(s)
    if not good:
        continue
    fracs = [num(s.get('sell_fraction')) or 0 for s in good]
    rets = [num(s.get('pnl_pct')) for s in good]
    peaks = [num(s.get('peak_pnl_pct')) for s in good if num(s.get('peak_pnl_pct')) is not None]
    maes = [num(s.get('mae_pct')) for s in good if num(s.get('mae_pct')) is not None]
    holds = [num(s.get('hold_secs')) for s in good if num(s.get('hold_secs')) is not None]
    fsum = sum(fracs)
    if fsum <= 0:
        # single full-close legs sometimes carry frac 0/None -> treat equal
        fracs = [1.0 / len(good)] * len(good); fsum = 1.0
    # realized = fraction-weighted pnl (normalize to full position)
    realized = sum(f * r for f, r in zip(fracs, rets) if r is not None) / fsum
    POS.append({
        'bot': b.get('bot_id'), 'address': b.get('address'), 'token': b.get('token'),
        'pc_h1': pc_h1,
        'realized': realized,
        'mfe': max(peaks) if peaks else None,
        'mae': min(maes) if maes else None,
        'hold': max(holds) if holds else None,
        'mae_at_secs': min((num(s.get('mae_at_secs')) for s in good
                            if num(s.get('mae_at_secs')) is not None), default=None),
        'legs': len(good),
        'day': (b.get('time') or '')[:10],
    })

withret = [p for p in POS if p['realized'] is not None and p['pc_h1'] is not None]
deep = [p for p in withret if p['pc_h1'] <= -45]
rest = [p for p in withret if p['pc_h1'] > -45]
print(f"positions: {len(POS)} | with pc_h1+realized: {len(withret)} "
      f"| DEEP(pc_h1<=-45): {len(deep)} ({len(set(p['address'] for p in deep))} tok) "
      f"| REST: {len(rest)} ({len(set(p['address'] for p in rest))} tok)")

def med(xs, f):
    v = [x[f] for x in xs if x.get(f) is not None]
    return st.median(v) if v else None

print("\n-- position-level cohort stats --")
for name, grp in [('DEEP', deep), ('REST', rest)]:
    r = [p['realized'] for p in grp]
    mf = [p['mfe'] for p in grp if p['mfe'] is not None]
    gb = [p['mfe'] - p['realized'] for p in grp if p['mfe'] is not None]
    print(f"{name:5s} n={len(grp)} realized med={st.median(r):+.2f} mean={st.mean(r):+.2f} "
          f"wr={100*sum(1 for x in r if x>0)/len(r):.0f}% | "
          f"MFE med={st.median(mf):+.1f} p75={sorted(mf)[int(len(mf)*.75)]:+.1f} "
          f"p90={sorted(mf)[int(len(mf)*.9)]:+.1f} | giveback med={st.median(gb):+.1f} mean={st.mean(gb):+.1f}")

# token-median ex-top2 (the mine's metric)
def tokmed_ex2(grp):
    bytok = defaultdict(list)
    for p in grp:
        bytok[p['address']].append(p['realized'])
    meds = sorted(((k, st.median(v), len(v)) for k, v in bytok.items()),
                  key=lambda x: -x[2])
    ex2 = [m for _, m, _ in meds[2:]]
    return st.median(ex2) if ex2 else None, len(meds)

dm, dn = tokmed_ex2(deep); rm, rn = tokmed_ex2(rest)
print(f"\ntokmed_ex2: DEEP {dm:+.2f} ({dn} tok)  REST {rm:+.2f} ({rn} tok)")

# ---- FAST-HARVEST replay (reliable: T <= live +6 TP1) ----
# variant: sell 100% at first touch of +T; else stop at S; else book realized-as-floor.
# Since MFE truncated, we ONLY trust T where MFE-reach is observable (T<=8).
def fast_harvest(grp, T, S=-15.0):
    out = []
    for p in grp:
        mfe = p['mfe']; mae = p['mae']; realized = p['realized']
        if mfe is None:
            out.append(realized); continue
        hit_tp = mfe >= T
        hit_stop = (mae is not None and mae <= S)
        if hit_tp and not hit_stop:
            out.append(min(mfe, T))               # harvest at target
        elif hit_tp and hit_stop:
            # ordering: if MAE happened at t=few secs (early) treat stop-first
            if p.get('mae_at_secs') is not None and p['mae_at_secs'] <= 5 and mae <= S:
                out.append(S)
            else:
                out.append(min(mfe, T))
        elif hit_stop:
            out.append(S)
        else:
            out.append(realized)                  # neither -> live outcome (bounded)
    return out

print("\n-- FAST-HARVEST replay on DEEP (sell 100% at +T, stop -15) --")
print(f"{'variant':14s} {'med':>7s} {'mean':>7s} {'wr':>5s} {'tokmed_ex2':>11s}")
base_r = [p['realized'] for p in deep]
print(f"{'LIVE(current)':14s} {st.median(base_r):+7.2f} {st.mean(base_r):+7.2f} "
      f"{100*sum(1 for x in base_r if x>0)/len(base_r):4.0f}% {dm:+11.2f}")
for T in [3, 4, 5, 6, 8]:
    o = fast_harvest(deep, T)
    bytok = defaultdict(list)
    for p, v in zip(deep, o):
        bytok[p['address']].append(v)
    meds = sorted(((st.median(v), len(v)) for v in bytok.values()), key=lambda x: -x[1])
    ex2 = [m for m, _ in meds[2:]]
    tm = st.median(ex2) if ex2 else float('nan')
    print(f"{'harvest@+'+str(T):14s} {st.median(o):+7.2f} {st.mean(o):+7.2f} "
          f"{100*sum(1 for x in o if x>0)/len(o):4.0f}% {tm:+11.2f}")

# ---- DEPTH-CONDITIONAL: split deep cohort into depth sub-bands ----
print("\n-- depth sub-bands within cohort (realized, MFE, giveback) --")
bands = [(-1e9, -80, 'vdeep<=-80'), (-80, -60, '-60..-80'),
         (-60, -45, '-45..-60'), (-45, -30, '-30..-45'), (-30, 1e9, 'shallow>-30')]
for lo, hi, lbl in bands:
    g = [p for p in withret if lo < p['pc_h1'] <= hi]
    if len(g) < 5:
        print(f"{lbl:12s} n={len(g)} (thin)"); continue
    r = [p['realized'] for p in g]
    mf = [p['mfe'] for p in g if p['mfe'] is not None]
    gb = [p['mfe'] - p['realized'] for p in g if p['mfe'] is not None]
    print(f"{lbl:12s} n={len(g):3d} realized med={st.median(r):+6.2f} mean={st.mean(r):+6.2f} "
          f"| MFE med={st.median(mf):+5.1f} p90={sorted(mf)[int(len(mf)*.9)]:+6.1f} "
          f"| giveback mean={st.mean(gb):+5.1f}")

# ---- BARBELL probe: where does the fat tail (MFE>=50) live? ----
print("\n-- fat-tail location (positions with MFE>=50) --")
for lo, hi, lbl in bands:
    g = [p for p in withret if lo < p['pc_h1'] <= hi and p['mfe'] is not None]
    if not g:
        continue
    fat = [p for p in g if p['mfe'] >= 50]
    print(f"{lbl:12s} n={len(g):3d} fat(MFE>=50)={len(fat):2d} ({100*len(fat)/len(g):.0f}%) "
          f"fat_realized_med={st.median([p['realized'] for p in fat]) if fat else float('nan'):+.1f}")
