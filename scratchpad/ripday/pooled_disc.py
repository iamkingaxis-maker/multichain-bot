# pooled_disc.py -- pooled Q2 discriminator: recoverer vs non (corpse+rugged)
# across GT any-life arcs + local runner-biased arcs. Reads caches only.
import json, os, glob, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from launch_arc import trough_features, med, fmt

RIP = os.path.dirname(os.path.abspath(__file__))
res = json.load(open(os.path.join(RIP, "_launch_arc_results.json")))

def bars_for(row, src):
    if src == "gt":
        f = os.path.join(RIP, "_gt_bars", row["pair"][:12] + ".json")
        return json.load(open(f)) if os.path.exists(f) else None
    f = os.path.join(RIP, "ohlc2_%s.json" % row["pair"][:8])
    if not os.path.exists(f):
        for g in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
            d = json.load(open(g))
            if d["pair"] == row["pair"]:
                return sorted(d["bars"], key=lambda b: b[0])
        return None
    d = json.load(open(f))
    return sorted(d["bars"], key=lambda b: b[0])

feats = []
for src in ("gt", "local"):
    for r in res[src]:
        cls = r.get("cls")
        if cls not in ("arc_recovered", "arc_corpse", "rugged"): continue
        if "trough_ts" not in r: continue
        bars = bars_for(r, src)
        if not bars: continue
        f = trough_features(bars, r)
        f["y"] = 1 if cls == "arc_recovered" else 0
        f["src"] = src; f["cls"] = cls; f["pair"] = r["pair"][:10]
        feats.append(f)

rec = [f for f in feats if f["y"]]
non = [f for f in feats if not f["y"]]
print("POOLED trough discriminator: recoverer n=%d vs non-recoverer (corpse+rugged) n=%d"
      % (len(rec), len(non)))
keys = ["dd", "min_since_peak", "vol_tr15", "vol_peak15", "vol_ratio",
        "green_share_tr15", "bars_tr15"]
print("%-18s %12s %12s" % ("feature (median)", "recoverer", "non-recov"))
for k in keys:
    print("%-18s %12s %12s" % (k, fmt(med([f.get(k) for f in rec])),
                               fmt(med([f.get(k) for f in non]))))
print("\nsweep -> recov-precision | recall | non-recov passed:")
def sweep(key, thrs, ge=True):
    base = [f for f in feats if f.get(key) is not None]
    br = [f for f in base if f["y"]]
    for t in thrs:
        sel = [f for f in base if (f[key] >= t if ge else f[key] <= t)]
        if not sel: continue
        pr = sum(1 for f in sel if f["y"])
        print("  %s %s %-7s -> prec %.0f%% (%d/%d) | recall %.0f%% | non passed %d/%d"
              % (key, ">=" if ge else "<=", t, 100.0 * pr / len(sel), pr, len(sel),
                 (100.0 * pr / len(br)) if br else 0,
                 len(sel) - pr, len(base) - len(br)))
sweep("vol_ratio", [0.05, 0.10, 0.25, 0.5])
sweep("vol_tr15", [1000, 5000, 20000])
sweep("bars_tr15", [8, 12, 15])
sweep("green_share_tr15", [0.25, 0.4])
sweep("min_since_peak", [30, 60], ge=False)
sweep("dd", [-0.85, -0.75, -0.60], ge=False)
print("\nper-token detail:")
for f in sorted(feats, key=lambda x: -x["y"]):
    print("  %-4s %-10s %-13s dd %s  tsp %sm  vr %s  gs %s  bars %s" %
          (f["src"], f["pair"], f["cls"], fmt(f["dd"], pct=True),
           fmt(f["min_since_peak"]), fmt(f.get("vol_ratio")),
           fmt(f.get("green_share_tr15")), f.get("bars_tr15")))
