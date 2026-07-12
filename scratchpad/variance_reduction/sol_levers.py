"""Variance-reduction lever analysis on Solana young-lane realized trips (955)."""
import json, statistics
from collections import defaultdict, Counter
from datetime import datetime

T = json.load(open("scratchpad/sol_selection/_trips.json"))
# scrub already applied upstream; sort chronological
for x in T:
    x["_t"] = datetime.fromisoformat(x["time"])
T.sort(key=lambda x: x["_t"])

def pstd(v): return statistics.pstdev(v) if len(v) > 1 else 0.0
def desc(v):
    v = list(v)
    return dict(n=len(v), mean=round(statistics.mean(v), 3), std=round(pstd(v), 3),
                worst=round(min(v), 2), best=round(max(v), 2),
                dnstd=round(pstd([min(x, 0) for x in v]), 3))

def day_std(trips):
    """stdev of per-day summed pnl (day-level variance)."""
    byd = defaultdict(float)
    for x in trips:
        byd[x["time"][:10]] += x["ret"]
    vals = list(byd.values())
    return round(pstd(vals), 2), round(statistics.mean(vals), 2), len(vals)

rets = [x["ret"] for x in T]
base = desc(rets)
bds, bdm, ndays = day_std(T)
print("=== SOLANA BASELINE ===")
print("per-trip:", base)
print("per-day pnl: std=%.2f mean=%.2f n_days=%d" % (bds, bdm, ndays))
print("volume: %d trips, %d distinct tokens" % (len(T), len(set(x["token"] for x in T))))
print()

# ---------------- LEVER 2: catastrophe cap via MAE ----------------
# A hard stop at floor F fires only if the trip's MAE reached F (intraday low).
# Trips that never dipped to F keep realized ret. Trips that did book ~F.
print("=== LEVER 2: catastrophe cap (MAE-gated hard stop) ===")
for F in (-20.0, -25.0, -30.0, -40.0):
    new = []
    clipped = 0
    for x in T:
        if x["mae"] is not None and x["mae"] <= F and x["ret"] < F:
            new.append(F)      # stop fires, book the floor
            clipped += 1
        else:
            new.append(x["ret"])
    d = desc(new)
    ds, dm, _ = day_std([{**x, "ret": n} for x, n in zip(T, new)])
    print("F=%6.1f -> trip std %6.3f (base %6.3f, cut %5.1f%%) | mean %6.3f (base %6.3f) | day std %6.2f (base %6.2f) | trips_clipped %d | worst %6.2f"
          % (F, d["std"], base["std"], 100*(1-d["std"]/base["std"]), d["mean"], base["mean"], ds, bds, clipped, d["worst"]))
print()

# ---------------- LEVER 3: hold-time box ----------------
# Measure variance concentration by hold bucket, then a bounding time-box sim.
print("=== LEVER 3: hold-time box ===")
holds = sorted(x["hold"] for x in T)
for T_box in (120, 180, 300, 600):
    over = [x for x in T if x["hold"] > T_box]
    under = [x for x in T if x["hold"] <= T_box]
    print("box=%4ds: %d trips over (%.0f%%), over-std=%.2f under-std=%.2f | over mean=%.2f under mean=%.2f | over worst=%.1f"
          % (T_box, len(over), 100*len(over)/len(T), pstd([x["ret"] for x in over]),
             pstd([x["ret"] for x in under]), statistics.mean([x["ret"] for x in over]),
             statistics.mean([x["ret"] for x in under]), min(x["ret"] for x in over)))
# bounding sim: box exits at max(realized, mae)  (can't do worse than realized once past box,
# best case caps upside at whatever it had) -> conservative variance proxy
print("  -- bounding sim (boxed trip books its realized ret; measures pure volume/edge of the over-box cohort) --")
for T_box in (300, 600):
    over = [x for x in T if x["hold"] > T_box]
    print("  drop-over box=%ds: removing %d trips -> remaining std %.3f (base %.3f) day std %.2f vol_ret %.0f%% edge_delta %.3f"
          % (T_box, len(over), desc([x["ret"] for x in T if x["hold"] <= T_box])["std"], base["std"],
             day_std(under if T_box==120 else [x for x in T if x['hold']<=T_box])[0],
             100*(len(T)-len(over))/len(T),
             desc([x["ret"] for x in T if x["hold"] <= T_box])["mean"] - base["mean"]))
print()

# ---------------- LEVER 4: per-token daily cap (correlation) ----------------
print("=== LEVER 4: per-token daily cap (de-cluster same-token/same-day) ===")
# clustering picture
byday_tok = Counter((x["time"][:10], x["token"]) for x in T)
worst_clusters = byday_tok.most_common(8)
print("worst (day,token) clusters:", worst_clusters)
for K in (1, 2, 3, 5):
    seen = Counter()
    kept = []
    for x in T:
        key = (x["time"][:10], x["token"])
        if seen[key] < K:
            kept.append(x); seen[key] += 1
    d = desc([x["ret"] for x in kept])
    ds, dm, nd = day_std(kept)
    print("K=%d/tok/day -> trips kept %d (%.0f%% vol) | trip std %6.3f (cut %5.1f%%) | mean %6.3f (base %6.3f) | DAY std %6.2f (base %6.2f, cut %5.1f%%)"
          % (K, len(kept), 100*len(kept)/len(T), d["std"], 100*(1-d["std"]/base["std"]),
             d["mean"], base["mean"], ds, bds, 100*(1-ds/bds)))
