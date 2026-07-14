# Discriminator analysis over _serial_rows.json
import json, statistics as st, os
os.chdir(r"C:\Users\jcole\multichain-bot\scratchpad\ripday")
rows = json.load(open("_serial_rows.json"))
n = len(rows)
base = sum(1 for r in rows if r["serial"]) / n

FEATS = ["bars_rate_60m", "bars_rate_full", "pre_history_min", "vol_cv_60m", "vol_usd_60m",
         "range_mean_60m", "ret_std_60m", "range_mean_pre",
         "age_h", "liq", "mcap",
         "tape_n", "tape_buyers", "tape_maxprint", "tape_buyfrac", "tape_medbuy",
         "first_bounce10"]

def med(v):
    return st.median(v) if v else None

print(f"n={n} serial base rate={base*100:.1f}%\n")
print(f"{'feature':<18}{'n_ok':>5} {'med_serial':>11} {'med_other':>10}")
for f in FEATS:
    s = [r[f] for r in rows if r["serial"] and r.get(f) is not None]
    o = [r[f] for r in rows if not r["serial"] and r.get(f) is not None]
    if not s or not o:
        print(f"{f:<18} no data"); continue
    print(f"{f:<18}{len(s)+len(o):>5} {med(s):>11.3g} {med(o):>10.3g}")

# threshold scan: for each feature, both directions, find thresholds maximizing precision at recall>=0.3
def evaluate(rows_sub, f, thr, direction):
    sel = [r for r in rows_sub if r.get(f) is not None and (r[f] >= thr if direction == ">=" else r[f] <= thr)]
    have = [r for r in rows_sub if r.get(f) is not None]
    if not have: return None
    ser_have = sum(1 for r in have if r["serial"])
    tp = sum(1 for r in sel if r["serial"])
    prec = tp / len(sel) if sel else 0
    rec = tp / ser_have if ser_have else 0
    return dict(n_sel=len(sel), n_have=len(have), prec=prec, rec=rec,
                base=ser_have / len(have))

def latch_econ(rs):
    if not rs: return (0, 0, 0)
    g = [r["latch_gross"] for r in rs]
    net = [r["latch_gross"] - 2.6 * r["latch_n"] for r in rs]
    return (st.mean(g), st.mean(net), len(rs))

print("\n--- threshold scan (best precision with n_sel>=25) ---")
results = []
for f in FEATS:
    vals = sorted({round(r[f], 6) for r in rows if r.get(f) is not None})
    if len(vals) < 8: continue
    qs = [vals[int(len(vals) * q)] for q in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)]
    for d in (">=", "<="):
        for thr in qs:
            ev = evaluate(rows, f, thr, d)
            if ev and ev["n_sel"] >= 25:
                results.append((ev["prec"], f, d, thr, ev))
results.sort(reverse=True)
seen = set()
top = []
for prec, f, d, thr, ev in results:
    if f in seen: continue
    seen.add(f); top.append((prec, f, d, thr, ev))
for prec, f, d, thr, ev in top[:12]:
    sel = [r for r in rows if r.get(f) is not None and (r[f] >= thr if d == ">=" else r[f] <= thr)]
    g, netm, k = latch_econ(sel)
    print(f"{f:>18} {d}{thr:<10.4g} prec={prec*100:5.1f}% (base {ev['base']*100:.0f}%) rec={ev['rec']*100:4.0f}% n_sel={ev['n_sel']:3d}  latch gross/tok={g:+.2f} net={netm:+.2f}")

json.dump([(f, d, thr) for _, f, d, thr, _ in top[:8]], open("_top_thresholds.json", "w"))
