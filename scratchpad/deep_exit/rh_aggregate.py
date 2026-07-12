"""Aggregate the RH deep-exit sweep: captured-pp per exit variant, 4-half OOS,
depth-conditional bands, barbell/fat-tail test. Real forward-tape continuation."""
import gzip, json, statistics as st
from collections import defaultdict

rows = [json.loads(l) for l in gzip.open('scratchpad/deep_exit/rh_deep_cands.jsonl.gz', 'rt')]
print(f"deep candidates: {len(rows)}  pools: {len(set(r['pool'] for r in rows))}")
VARIANTS = list(rows[0]['ex'].keys())

def half_tags(day):
    # chrono W1 07-01..05 / W2 07-06..11 ; odd/even day-of-month
    try:
        dom = int(day[8:10])
    except Exception:
        return []
    tags = []
    tags.append('W1' if day <= '2026-07-05' else 'W2')
    tags.append('odd' if dom % 2 == 1 else 'even')
    return tags

def tokmed_ex2(pairs):
    """pairs: list of (pool, ret). pool-median, drop 2 highest-count pools."""
    bypool = defaultdict(list)
    for pool, v in pairs:
        bypool[pool].append(v)
    meds = sorted(((st.median(v), len(v)) for v in bypool.values()), key=lambda x: -x[1])
    ex2 = [m for m, _ in meds[2:]]
    return (st.median(ex2) if ex2 else float('nan')), len(meds)

def summ(subset, variant):
    vals = [r['ex'][variant] for r in subset]
    pairs = [(r['pool'], r['ex'][variant]) for r in subset]
    tm, ntok = tokmed_ex2(pairs)
    cat = sum(1 for v in vals if v <= -50) / len(vals) if vals else 0
    return dict(n=len(vals), mean=st.mean(vals), med=st.median(vals),
                tokmed=tm, ntok=ntok, wr=100*sum(1 for v in vals if v > 0)/len(vals),
                cat=100*cat)

# ---- overall deep cohort, per variant ----
print("\n=== DEEP cohort (dip<=-20), per exit variant ===")
print(f"{'variant':13s} {'mean':>7s} {'med':>6s} {'tokmed_ex2':>10s} {'wr%':>5s} "
      f"{'cat%':>5s} {'min4half_tokmed':>15s}")
ranked = []
for v in VARIANTS:
    o = summ(rows, v)
    # 4-half min tokmed
    hmins = []
    for tag in ['W1', 'W2', 'odd', 'even']:
        sub = [r for r in rows if tag in half_tags(r['day'])]
        hmins.append(summ(sub, v)['tokmed'])
    mn = min(hmins)
    ranked.append((o['mean'], v, o, mn, hmins))
ranked.sort(key=lambda x: -x[0])
for mean, v, o, mn, hmins in ranked:
    print(f"{v:13s} {o['mean']:+7.2f} {o['med']:+6.2f} {o['tokmed']:+10.2f} "
          f"{o['wr']:5.0f} {o['cat']:5.1f} {mn:+15.2f}")

# ---- depth-conditional: variant x dip band ----
print("\n=== DEPTH-CONDITIONAL: mean net per variant x dip band ===")
bands = [(-1e9, -45, '<=-45'), (-45, -30, '-30..-45'), (-30, -20, '-20..-30')]
hdr = f"{'variant':13s}" + ''.join(f"{lbl:>12s}" for _, _, lbl in bands)
print(hdr)
for v in VARIANTS:
    line = f"{v:13s}"
    for lo, hi, lbl in bands:
        g = [r for r in rows if lo < r['dip'] <= hi]
        m = st.mean([r['ex'][v] for r in g]) if g else float('nan')
        line += f"{m:+12.2f}"
    print(line)
print("band Ns:", {lbl: sum(1 for r in rows if lo < r['dip'] <= hi) for lo, hi, lbl in bands})

# ---- best variant per depth band (by mean, and by tokmed) ----
print("\n=== best variant per depth band ===")
for lo, hi, lbl in bands:
    g = [r for r in rows if lo < r['dip'] <= hi]
    if not g:
        continue
    bymean = sorted(VARIANTS, key=lambda v: -st.mean([r['ex'][v] for r in g]))
    bytm = sorted(VARIANTS, key=lambda v: -summ(g, v)['tokmed'])
    bm = st.mean([r['ex'][bymean[0]] for r in g])
    tm = summ(g, bytm[0])['tokmed']
    print(f"{lbl:10s} n={len(g):4d}  best-mean: {bymean[0]:12s} ({bm:+.2f})   "
          f"best-tokmed: {bytm[0]:12s} ({tm:+.2f})")

# ---- fat-tail location (mfe>=50) by dip band ----
print("\n=== fat tail (MFE>=50) by dip band ===")
for lo, hi, lbl in bands:
    g = [r for r in rows if lo < r['dip'] <= hi]
    if not g:
        continue
    fat = [r for r in g if r['mfe'] >= 50]
    print(f"{lbl:10s} n={len(g):4d}  fat={len(fat):3d} ({100*len(fat)/len(g):.1f}%)  "
          f"mfe med={st.median([r['mfe'] for r in g]):+.1f} p90={sorted([r['mfe'] for r in g])[int(len(g)*.9)]:+.1f}")

# ---- barbell head-to-head on the DEEPEST band (where fat tail concentrates) ----
print("\n=== barbell vs fast vs patient on <=-45 (mean / tokmed / 4half-min-tokmed) ===")
g = [r for r in rows if r['dip'] <= -45]
for v in ['fast5_all', 'fast5_90', 'scalp', 'barbell8020', 'barbell7030',
          'barbell9010', 'patient', 'runner40', 'aged']:
    o = summ(g, v)
    hmins = min(summ([r for r in g if tag in half_tags(r['day'])], v)['tokmed']
                for tag in ['W1', 'W2', 'odd', 'even'])
    print(f"{v:13s} n={o['n']:4d} mean={o['mean']:+7.2f} tokmed={o['tokmed']:+6.2f} "
          f"wr={o['wr']:3.0f}% cat={o['cat']:4.1f}% min4half_tokmed={hmins:+.2f}")

# ---- res composition (dead-pool honesty) ----
print("\nres composition:", {k: sum(1 for r in rows if r['res'] == k)
                             for k in set(r['res'] for r in rows)})
