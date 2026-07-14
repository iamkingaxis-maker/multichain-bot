"""BOUNCED-BUT-WE-LOST — step 3: aggregate answers Q1-Q4."""
import json, os
from collections import defaultdict

RIP = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(RIP, "_bnl_replay.json")))

def pct(a, b):
    return 100.0 * a / b if b else 0.0

print("=" * 70)
print("Q1 — COHORT SIZE (losing rounds since 07-01, 3 family bots)")
print("=" * 70)
n_all = len(d)
cov = [x for x in d if x["covered"]]
reach = [x for x in cov if x["tp1_reachable"]]
print(f"losing rounds: {n_all}  bar-covered: {len(cov)} ({pct(len(cov),n_all):.0f}%)  "
      f"TP1(+6 from OUR entry) reachable within 90m: {len(reach)} = {pct(len(reach),len(cov)):.1f}% of covered")

# per terminal class
print("\nby terminal exit class (rounds):")
print(f"{'class':<16}{'n_lose':>7}{'covered':>8}{'reachable':>10}{'reach%':>8}")
cls = defaultdict(lambda: [0, 0, 0])
for x in d:
    c = cls[x["term_class"]]
    c[0] += 1
    if x["covered"]:
        c[1] += 1
        if x["tp1_reachable"]:
            c[2] += 1
for k, (a, b, c) in sorted(cls.items(), key=lambda kv: -kv[1][0]):
    print(f"{k:<16}{a:>7}{b:>8}{c:>10}{pct(c,b):>7.1f}%")

# per-token dedup (first losing round per token address)
first = {}
for x in sorted(d, key=lambda x: x["entry_ts"]):
    first.setdefault(x["address"], x)
toks = list(first.values())
tcov = [x for x in toks if x["covered"]]
treach = [x for x in tcov if x["tp1_reachable"]]
print(f"\nper-token dedup (first losing round per token): tokens {len(toks)}  "
      f"covered {len(tcov)}  reachable {len(treach)} = {pct(len(treach),len(tcov)):.1f}%")
tcls = defaultdict(lambda: [0, 0, 0])
for x in toks:
    c = tcls[x["term_class"]]
    c[0] += 1
    if x["covered"]:
        c[1] += 1
        if x["tp1_reachable"]:
            c[2] += 1
print(f"{'class':<16}{'tokens':>7}{'covered':>8}{'reachable':>10}{'reach%':>8}")
for k, (a, b, c) in sorted(tcls.items(), key=lambda kv: -kv[1][0]):
    print(f"{k:<16}{a:>7}{b:>8}{c:>10}{pct(c,b):>7.1f}%")

print()
print("=" * 70)
print("Q2 — COUNTERFACTUAL LADDER on reachable-TP1 cohort")
print("=" * 70)
def agg_replay(rows, key):
    rows = [x for x in rows if x.get(key)]
    act = sum(x["actual_pct"] for x in rows)
    rep = sum(x[key]["realized_pct"] for x in rows)
    capped = sum(1 for x in rows if x[key]["capped"])
    tp1 = sum(1 for x in rows if x[key]["tp1_hit"])
    return len(rows), act, rep, capped, tp1

KEYS = [("replayA", "A pess: floor -6/-7, stop -12"),
        ("replayA_touch", "A touch: floor -6/-7, stop -12"),
        ("replayB", "B pess: floor -18 (wide)"),
        ("replayB_touch", "B touch: floor -18 (wide)")]
for label, rows in [("reachable cohort (rounds)", reach)]:
    for key, name in KEYS:
        n, act, rep, capped, tp1 = agg_replay(rows, key)
        print(f"{label} [{name}]: n={n}")
        print(f"  actual realized:  mean {act/n:+.2f}pp  total {act:+.0f}pp")
        print(f"  replay realized:  mean {rep/n:+.2f}pp  total {rep:+.0f}pp   "
              f"delta {(rep-act)/n:+.2f}pp/round  total {rep-act:+.0f}pp")
        print(f"  replay TP1 hit: {tp1}/{n}  still-open-at-cap marks: {capped}")

# per-token dedup on reachable cohort
tre = {}
for x in sorted(reach, key=lambda x: x["entry_ts"]):
    tre.setdefault(x["address"], []).append(x)
for key, name in KEYS:
    deltas = []
    for a, rows in tre.items():
        ds = [x[key]["realized_pct"] - x["actual_pct"] for x in rows if x.get(key)]
        if ds:
            deltas.append(sum(ds) / len(ds))
    deltas.sort()
    n = len(deltas)
    med = deltas[n // 2] if n else 0
    print(f"per-token dedup [{name}]: tokens={n}  mean delta {sum(deltas)/n:+.2f}pp  "
          f"median {med:+.2f}pp  sum {sum(deltas):+.0f}pp")

print()
print("=" * 70)
print("Q3 — SAVES: velocity bails that dodged a further >=6% drop (90m)")
print("=" * 70)
vb = [x for x in d if x["term_class"] == "velocity_bail"]
vbb = [x for x in vb if "post_bail_min_pct" in x]
saved = [x for x in vbb if x["bail_saved_6"]]
cost = [x for x in vbb if x["bail_cost_6"]]
both = [x for x in vbb if x["bail_saved_6"] and x["bail_cost_6"]]
print(f"velocity-bail losing rounds: {len(vb)}  with post-bail bars: {len(vbb)}")
print(f"  SAVED (fell >=6% below bail px within 90m): {len(saved)} = {pct(len(saved),len(vbb)):.1f}%")
print(f"  COST  (rose >=6% above bail px within 90m): {len(cost)} = {pct(len(cost),len(vbb)):.1f}%")
print(f"  BOTH (whipsaw): {len(both)}")
mn = sum(x["post_bail_min_pct"] for x in vbb) / len(vbb)
mx = sum(x["post_bail_max_pct"] for x in vbb) / len(vbb)
print(f"  mean post-bail 90m: min {mn:+.1f}%  max {mx:+.1f}%")
# token dedup
tvb = {}
for x in sorted(vbb, key=lambda x: x["entry_ts"]):
    tvb.setdefault(x["address"], x)
tv = list(tvb.values())
print(f"  per-token dedup: {len(tv)} tokens  saved {sum(1 for x in tv if x['bail_saved_6'])} "
      f"({pct(sum(1 for x in tv if x['bail_saved_6']),len(tv)):.0f}%)  "
      f"cost {sum(1 for x in tv if x['bail_cost_6'])} "
      f"({pct(sum(1 for x in tv if x['bail_cost_6']),len(tv)):.0f}%)")

print()
print("=" * 70)
print("Q4 — NET VERDICT: replay delta on ALL covered velocity-bail rounds")
print("=" * 70)
vbc = [x for x in vb if x["covered"] and x.get("replayA")]
ts_lo = min(x["entry_ts"] for x in d)
ts_hi = max(x["entry_ts"] for x in d)
mid = (ts_lo + ts_hi) / 2
window_days = (ts_hi - ts_lo) / 86400.0
for key, name in KEYS:
    rows = [x for x in vbc if x.get(key)]
    tot = sum(x[key]["realized_pct"] - x["actual_pct"] for x in rows)
    h1 = [x for x in rows if x["entry_ts"] < mid]
    h2 = [x for x in rows if x["entry_ts"] >= mid]
    t1 = sum(x[key]["realized_pct"] - x["actual_pct"] for x in h1)
    t2 = sum(x[key]["realized_pct"] - x["actual_pct"] for x in h2)
    # token dedup
    td = {}
    for x in sorted(rows, key=lambda x: x["entry_ts"]):
        td.setdefault(x["address"], []).append(x[key]["realized_pct"] - x["actual_pct"])
    tded = [sum(v) / len(v) for v in td.values()]
    wins = sum(1 for x in rows if x[key]["realized_pct"] > x["actual_pct"])
    print(f"[{name}] n={len(rows)} rounds / {len(tded)} tokens, window {window_days:.1f}d")
    print(f"  raw round-sum delta: {tot:+.0f}pp  -> {tot*7/window_days:+.0f}pp/week  "
          f"({wins} rounds better, {len(rows)-wins} worse/equal)")
    print(f"  token-dedup delta: sum {sum(tded):+.0f}pp  mean {sum(tded)/len(tded):+.2f}pp  "
          f"-> {sum(tded)*7/window_days:+.0f}pp/week")
    print(f"  halves: H1(<{mid:.0f}) n={len(h1)} {t1:+.0f}pp | H2 n={len(h2)} {t2:+.0f}pp")

# distribution of deltas for A
rows = vbc
ds = sorted(x["replayA"]["realized_pct"] - x["actual_pct"] for x in rows)
import statistics
print(f"\nreplayA delta distribution (n={len(ds)}): p10 {ds[len(ds)//10]:+.1f}  "
      f"p25 {ds[len(ds)//4]:+.1f}  med {statistics.median(ds):+.1f}  "
      f"p75 {ds[3*len(ds)//4]:+.1f}  p90 {ds[9*len(ds)//10]:+.1f}")
worst = sorted(rows, key=lambda x: x["replayA"]["realized_pct"] - x["actual_pct"])[:8]
print("\nworst 8 (velocity bail SAVED most, replayA):")
for x in worst:
    print(f"  {x['token'][:14]:<14} act {x['actual_pct']:+.1f} -> repA "
          f"{x['replayA']['realized_pct']:+.1f} (legs {x['replayA']['legs']}) "
          f"post-bail min {x.get('post_bail_min_pct',0):+.1f}")
best = sorted(rows, key=lambda x: x["actual_pct"] - x["replayA"]["realized_pct"])[:8]
print("\nbest 8 (velocity bail COST most, replayA):")
for x in best:
    print(f"  {x['token'][:14]:<14} act {x['actual_pct']:+.1f} -> repA "
          f"{x['replayA']['realized_pct']:+.1f} tp1_hit={x['replayA']['tp1_hit']}")
