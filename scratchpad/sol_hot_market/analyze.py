import json, statistics as st
from collections import defaultdict
from datetime import datetime

d = json.load(open('scratchpad/sol_selection/_trips.json'))
for t in d:
    t['dt'] = datetime.fromisoformat(t['time'])
    t['day'] = t['dt'].strftime('%Y-%m-%d')

def pctl(xs, p):
    if not xs: return float('nan')
    xs = sorted(xs); k = (len(xs)-1)*p/100.0
    f = int(k); c = min(f+1, len(xs)-1)
    return xs[f] + (xs[c]-xs[f])*(k-f)
def med(xs): return st.median(xs) if xs else float('nan')

def tokmed_ex2(trips, key):
    bytok = defaultdict(list)
    for t in trips:
        v = t.get(key)
        if v is not None: bytok[t['token']].append(v)
    tokvals = sorted((med(v) for v in bytok.values()), reverse=True)
    rest = tokvals[2:] if len(tokvals) > 2 else tokvals
    return med(rest), len(tokvals)

def reach_rate(trips, thr):
    # per-token: did token's max peak reach thr? token-level fraction
    bytok = defaultdict(list)
    for t in trips: bytok[t['token']].append(t.get('peak') or 0)
    hits = sum(1 for v in bytok.values() if max(v) >= thr)
    return hits/len(bytok) if bytok else float('nan'), len(bytok)

days = defaultdict(int)
for t in d: days[t['day']] += 1
print("=== trips per day ===")
for day in sorted(days): print(day, days[day])

# Cohorts: recent 3 days vs prior 4 (last 7 days window: 07-06..07-12)
recent = [t for t in d if t['day'] >= '2026-07-10']
prior  = [t for t in d if '2026-07-06' <= t['day'] <= '2026-07-09']
print(f"\nrecent(07-10..12) n={len(recent)}  prior(07-06..09) n={len(prior)}")

print("\n=== Q1: recent vs prior (ex-top2 token-median) ===")
for key,lbl in [('peak','peak%'),('liq','liq$'),('ret','ret%'),('mae','mae%'),('hold','hold_s'),
                ('entry_vol_h24','vol_h24'),('mcap_usd','mcap')]:
    r,rn = tokmed_ex2(recent, key); p,pn = tokmed_ex2(prior, key)
    print(f"{lbl:9s} recent={r:12.2f} (ntok {rn:3d})  prior={p:12.2f} (ntok {pn:3d})")

print("\n=== Q1: fill volume ===")
print(f"recent trips/day = {len(recent)/3:.1f}   prior trips/day = {len(prior)/4:.1f}")

print("\n=== Q1: %tokens reaching peak thresholds (token-level) ===")
for thr in [10,20,30,50]:
    rr,_ = reach_rate(recent, thr); pr,_ = reach_rate(prior, thr)
    print(f"peak>=+{thr:2d}:  recent={rr*100:5.1f}%   prior={pr*100:5.1f}%")

print("\n=== Q1: raw peak percentiles (trip-level) ===")
for lbl,coh in [('recent',recent),('prior',prior)]:
    pk = [t['peak'] for t in coh]
    print(f"{lbl}: p50={pctl(pk,50):.1f} p75={pctl(pk,75):.1f} p90={pctl(pk,90):.1f} p95={pctl(pk,95):.1f} max={max(pk):.1f}")
