"""Variance-reduction levers on reconstructed RH closed trips."""
import json, statistics
from collections import defaultdict, Counter
from datetime import datetime

T = json.load(open("scratchpad/variance_reduction/_rh_trips.json"))
# keep trips with a real entry ts (hold known) for hold-based levers; all for pnl
for x in T:
    x["_t"] = x["entry_ts"] or x["first_sell_ts"]
T.sort(key=lambda x: x["_t"])

def pstd(v): return statistics.pstdev(v) if len(v) > 1 else 0.0
def desc(v):
    v = [x for x in v if x is not None]
    return dict(n=len(v), mean=round(statistics.mean(v), 3), std=round(pstd(v), 3),
                worst=round(min(v), 2), best=round(max(v), 2),
                dnstd=round(pstd([min(x, 0) for x in v]), 3))
def day_std(trips, key="pnl_pct"):
    byd = defaultdict(float)
    for x in trips:
        byd[x["day"]] += x[key]
    vals = list(byd.values())
    return round(pstd(vals), 2), round(statistics.mean(vals), 2), len(vals)

P = [x["pnl_pct"] for x in T]
base = desc(P)
bds, bdm, ndays = day_std(T)
print("=== RH BASELINE ===")
print("per-trip pnl_pct:", base)
print("per-day pnl_pct: std=%.2f mean=%.2f n_days=%d" % (bds, bdm, ndays))
print("volume: %d trips, %d distinct pools" % (len(T), len(set(x["pool"] for x in T))))
print()

# ---- LEVER 1: earlier principal de-risk — NATURAL A/B (aged_derisk 0.75 TP1 vs aged_hold 0.50) ----
print("=== LEVER 1: earlier de-risk (RH natural A/B: aged_derisk 0.75@+6 + 20min cap vs aged_hold 0.50@+6) ===")
for bid in ("rh_aged_hold", "rh_aged_derisk", "rh_aged_deep"):
    sub = [x["pnl_pct"] for x in T if x["bot"] == bid]
    print("  %-16s %s" % (bid, desc(sub)))
# also moonbag (rides tail, 0 TP1 fraction essentially) vs wide_ladder (0.75 TP1)
for bid in ("rh_moonbag", "rh_wide_ladder", "rh_young_v1"):
    sub = [x["pnl_pct"] for x in T if x["bot"] == bid]
    print("  %-16s %s" % (bid, desc(sub)))
print()

# ---- LEVER 2: catastrophe cap (realized floor) + rug-stamp gate ----
print("=== LEVER 2: catastrophe cap (realized-floor) & rug-stamp gate ===")
for F in (-20.0, -30.0, -40.0):
    new = [min(x["pnl_pct"], F) if False else (F if x["pnl_pct"] < F else x["pnl_pct"]) for x in T]
    d = desc(new)
    clipped = sum(1 for x in T if x["pnl_pct"] < F)
    print("  floor=%6.1f -> std %6.3f (base %6.3f cut %5.1f%%) mean %6.3f (base %6.3f) clipped %d worst %6.2f"
          % (F, d["std"], base["std"], 100*(1-d["std"]/base["std"]), d["mean"], base["mean"], clipped, d["worst"]))
# rug-stamp gate: drop trips whose pool got a rug_signals stamp
rug = [x for x in T if x["rug_stamped"]]
norug = [x for x in T if not x["rug_stamped"]]
print("  rug-stamped pools: %d trips, mean %.2f std %.2f worst %.2f | vs clean %d trips mean %.2f std %.2f worst %.2f"
      % (len(rug), desc([x["pnl_pct"] for x in rug])["mean"], desc([x["pnl_pct"] for x in rug])["std"], min((x["pnl_pct"] for x in rug), default=0),
         len(norug), desc([x["pnl_pct"] for x in norug])["mean"], desc([x["pnl_pct"] for x in norug])["std"], min((x["pnl_pct"] for x in norug), default=0)))
print()

# ---- LEVER 3: hold-time box ----
print("=== LEVER 3: hold-time box (RH) ===")
hs = [x for x in T if x["hold_s"] is not None]
print("  hold_s: n=%d median=%.0f p90=%.0f max=%.0f" % (len(hs), statistics.median([x["hold_s"] for x in hs]),
      sorted([x["hold_s"] for x in hs])[int(.9*len(hs))], max(x["hold_s"] for x in hs)))
for box in (120, 300, 600, 1200):
    over = [x for x in hs if x["hold_s"] > box]
    under = [x for x in hs if x["hold_s"] <= box]
    if not over: continue
    print("  box=%4ds: over %d (%.0f%%) std=%.2f mean=%.2f worst=%.1f | under %d std=%.2f mean=%.2f | drop-over: rem std %.3f (base %.3f) vol %.0f%% edge_delta %+.3f"
          % (box, len(over), 100*len(over)/len(hs), desc([x['pnl_pct'] for x in over])['std'], desc([x['pnl_pct'] for x in over])['mean'],
             min(x['pnl_pct'] for x in over), len(under), desc([x['pnl_pct'] for x in under])['std'], desc([x['pnl_pct'] for x in under])['mean'],
             desc([x['pnl_pct'] for x in under])['std'], base['std'], 100*len(under)/len(hs),
             desc([x['pnl_pct'] for x in under])['mean'] - base['mean']))
print()

# ---- LEVER 4: per-pool daily cap ----
print("=== LEVER 4: per-pool daily cap (RH) ===")
byday_pool = Counter((x["day"], x["pool"]) for x in T)
print("  worst (day,pool) clusters:", byday_pool.most_common(5))
for K in (1, 2, 3):
    seen = Counter(); kept = []
    for x in T:
        key = (x["day"], x["pool"])
        if seen[key] < K:
            kept.append(x); seen[key] += 1
    d = desc([x["pnl_pct"] for x in kept])
    ds, dm, nd = day_std(kept)
    print("  K=%d/pool/day -> kept %d (%.0f%% vol) trip std %.3f (cut %5.1f%%) mean %.3f (base %.3f) DAY std %.2f (base %.2f cut %5.1f%%)"
          % (K, len(kept), 100*len(kept)/len(T), d["std"], 100*(1-d["std"]/base["std"]),
             d["mean"], base["mean"], ds, bds, 100*(1-ds/bds)))
