"""Threshold / downsize / K sweep for the causal rolling-dial sizing gate."""
import bisect
from datetime import timedelta
from collections import defaultdict
import importlib.util, sys
spec = importlib.util.spec_from_file_location("sig", "scratchpad/_rh_regime_signal_0713.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

rows = m.load_rows(); trips, _ = m.scrub(m.build_trips(rows))
for t in trips:
    t["_close_ts"] = t["entry_ts"] + timedelta(seconds=t["hold_s"])
days = sorted({t["day"] for t in trips})

def causal_dials(K, min_n=8):
    cs = sorted(trips, key=lambda t: t["_close_ts"])
    cts = [t["_close_ts"] for t in cs]
    out = {}
    for t in trips:
        j = bisect.bisect_right(cts, t["entry_ts"])
        prior = cs[max(0, j-K):j]
        out[id(t)] = (sum(x["pnl_usd"] for x in prior)/len(prior)) if len(prior) >= min_n else None
    return out

base = defaultdict(float)
for t in trips:
    base[t["day"]] += t["pnl_usd"]
base_tot = sum(base.values())
print(f"BASE per-day: " + "  ".join(f"{d[5:]}=${base[d]:.1f}" for d in days) + f"  TOT=${base_tot:.1f}\n")

print("=== threshold x downsize sweep (K=15) — gated net per day + total ===")
dials = causal_dials(15)
for thr in (0.0, -0.25, -0.5, -0.75, -1.0):
    for ds in (0.5, 0.3, 0.0):
        g = defaultdict(float); nd = defaultdict(int)
        for t in trips:
            d = dials[id(t)]
            down = (d is not None and d < thr)
            g[t["day"]] += t["pnl_usd"] * (ds if down else 1.0)
            nd[t["day"]] += 1 if down else 0
        tot = sum(g.values())
        cells = "  ".join(f"{dd[5:]}=${g[dd]:>7.1f}({nd[dd]:>3})" for dd in days)
        print(f"  thr<{thr:>5} ds={ds}: {cells}  TOT=${tot:>7.1f} d={tot-base_tot:+7.1f}")
    print()

print("=== window K sweep (thr<0, ds=0.3) ===")
for K in (8, 10, 12, 15, 20, 25):
    dl = causal_dials(K)
    g = defaultdict(float); nd = defaultdict(int); warm = 0
    for t in trips:
        d = dl[id(t)]
        if d is None: warm += 1
        down = (d is not None and d < 0)
        g[t["day"]] += t["pnl_usd"] * (0.3 if down else 1.0)
        nd[t["day"]] += 1 if down else 0
    tot = sum(g.values())
    cells = "  ".join(f"{dd[5:]}=${g[dd]:>7.1f}({nd[dd]:>3}dn)" for dd in days)
    print(f"  K={K:>2} warmup={warm:>3}: {cells}  TOT=${tot:>7.1f} d={tot-base_tot:+7.1f}")
