"""Stress-test the two odd/even survivors: micro.flow_confirm (per-token
net-inflow demand turn) and liq. Adds: tokmed lift per split, token
concentration, a SECOND OOS axis (chrono day-half), and band interaction."""
import json
import statistics as st
from collections import defaultdict
from datetime import datetime
import importlib.util

spec = importlib.util.spec_from_file_location(
    "rha", "scratchpad/_rh_regime_analysis.py")
rha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rha)

rows = rha.load_rows()
trips = rha.build_trips(rows)
trips, _ = rha.scrub(trips)
reg = [t for t in trips if t["has_regime"] and t["buy_share_30m"] is not None]
# flow_confirm present on all buys (micro always stamped)
fc = [t for t in trips if t["flow_confirm"] is not None]
print(f"trips: all={len(trips)} regime={len(reg)} flow_confirm-present={len(fc)}")


def tokmed_ex2(sub):
    by = defaultdict(list)
    for t in sub:
        by[t["token"]].append(t["ret"])
    meds = sorted(st.median(v) for v in by.values())
    ex2 = meds[:-2] if len(meds) > 2 else meds
    return (round(st.median(ex2), 2) if ex2 else None,
            round(st.mean([t["ret"] for t in sub]), 2) if sub else None,
            len(sub), len(by))


def cohort(sub, cond):
    yes = [t for t in sub if cond(t)]
    no = [t for t in sub if not cond(t)]
    ty, my, ny, kt = tokmed_ex2(yes)
    tn, mn, nn, kn = tokmed_ex2(no)
    return (ty, my, ny, kt), (tn, mn, nn, kn)


def report(name, sub, cond):
    (ty, my, ny, ky), (tn, mn, nn, kn) = cohort(sub, cond)
    lm = round((my or 0) - (mn or 0), 2)
    lt = round((ty or 0) - (tn or 0), 2) if ty is not None and tn is not None else None
    print(f"  {name}: YES tokmed={ty} mean={my} (n{ny}/{ky}tok) | "
          f"NO tokmed={tn} mean={mn} (n{nn}/{kn}tok) | "
          f"LIFT mean={lm} tokEx2={lt}")
    return lm, lt


print("\n" + "=" * 60)
print("FLOW_CONFIRM (per-token net-inflow demand turn)")
print("=" * 60)
fc_sorted = sorted(fc, key=lambda t: t["entry_ts"] or "")
for i, t in enumerate(fc_sorted):
    t["_i"] = i
even = [t for t in fc_sorted if t["_i"] % 2 == 0]
odd = [t for t in fc_sorted if t["_i"] % 2 == 1]
cond = lambda t: t["flow_confirm"] is True
print("ODD/EVEN OOS:")
report("even", even, cond)
report("odd", odd, cond)
# second OOS axis: chrono day-half
days = sorted(set((t["entry_ts"] or "")[:10] for t in fc))
print(f"\nCHRONO day-half OOS (days: {days}):")
mid = st.median([t["entry_ts"] for t in fc])
w1 = [t for t in fc if t["entry_ts"] < mid]
w2 = [t for t in fc if t["entry_ts"] >= mid]
report("W1(early)", w1, cond)
report("W2(late)", w2, cond)
# token concentration of confirm=True
yes = [t for t in fc if cond(t)]
by = defaultdict(list)
for t in yes:
    by[t["token"]].append(t["ret"])
print(f"\nconfirm=True: {len(yes)} trips across {len(by)} tokens")
topcontrib = sorted(((k, len(v), round(st.mean(v), 1)) for k, v in by.items()),
                    key=lambda x: -x[1])[:5]
print("  top tokens by trip count:", topcontrib)
print("\nBAND interaction (does demand turn help EVERY band or only some?):")
for band in ("young", "mid", "aged"):
    bt = [t for t in fc if t["band"] == band]
    if len(bt) >= 8:
        report(f"{band}(n{len(bt)})", bt, cond)
    else:
        print(f"  {band}: n={len(bt)} thin")
# unstamped-band trips (flow_confirm present but no regime/band)
noband = [t for t in fc if t["band"] is None]
print(f"  (no-band/unstamped: n={len(noband)})")
if len(noband) >= 8:
    report("no-band", noband, cond)

print("\n" + "=" * 60)
print("LIQ (depth) — favor HIGH")
print("=" * 60)
liqt = [t for t in reg if t["liq"] is not None]
liqt_s = sorted(liqt, key=lambda t: t["entry_ts"] or "")
for i, t in enumerate(liqt_s):
    t["_i"] = i
med_liq = st.median([t["liq"] for t in liqt])
print(f"liq median={med_liq:.0f}")
cond_liq = lambda t: t["liq"] >= med_liq
print("ODD/EVEN OOS:")
report("even", [t for t in liqt_s if t["_i"] % 2 == 0], cond_liq)
report("odd", [t for t in liqt_s if t["_i"] % 2 == 1], cond_liq)
mid2 = st.median([t["entry_ts"] for t in liqt])
print("CHRONO day-half OOS:")
report("W1", [t for t in liqt if t["entry_ts"] < mid2], cond_liq)
report("W2", [t for t in liqt if t["entry_ts"] >= mid2], cond_liq)

# combined: flow_confirm AND high liq
print("\n" + "=" * 60)
print("COMBINED: flow_confirm=True AND liq>=median")
print("=" * 60)
comb = [t for t in reg if t["flow_confirm"] is not None and t["liq"] is not None]
report("all", comb, lambda t: t["flow_confirm"] is True and t["liq"] >= med_liq)
