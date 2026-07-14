# traction_addendum.py -- extra cuts on _traction_rows.json (no net)
import json, os, statistics as st

RIP = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(RIP, "_traction_rows.json")))
A, B = d["A"], d["B"]
Bg = [r for r in B if r.get("have_bars")]

def g(r, k): return r.get(k) or 0

def sweep_full(name, fn):
    sel = [r for r in A if fn(r)]
    tp_f = sum(1 for r in sel if r["traction_full"])
    tp_m = sum(1 for r in sel if r["traction"])
    pos_f = sum(1 for r in A if r["traction_full"])
    pos_m = sum(1 for r in A if r["traction"])
    b = sum(1 for r in Bg if fn(r)) if Bg else 0
    print("  %-38s pass %5.1f%% | mcap-recall %d/%d | FULL-floor recall %d/%d | "
          "B-recall %d/%d | prec(full) %4.1f%%"
          % (name, 100.0 * len(sel) / len(A), tp_m, pos_m, tp_f, pos_f, b, len(Bg),
             100.0 * tp_f / len(sel) if sel else 0))

print("A n=%d ; traction(mcap) %d ; traction_full %d ; B n=%d" %
      (len(A), sum(1 for r in A if r["traction"]),
       sum(1 for r in A if r["traction_full"]), len(Bg)))

print("\n== sweeps vs FULL tradeable floor (mcap>=100k & liq>=25k) ==")
sweep_full("n_bars_15>=8", lambda r: g(r, "n_bars_15") >= 8)
sweep_full("vol_15>=5k", lambda r: g(r, "vol_15") >= 5000)
sweep_full("vol_15>=10k", lambda r: g(r, "vol_15") >= 10000)
sweep_full("vol_15>=25k", lambda r: g(r, "vol_15") >= 25000)
sweep_full("vol_15>=50k", lambda r: g(r, "vol_15") >= 50000)
sweep_full("vol_5>=10k", lambda r: g(r, "vol_5") >= 10000)
sweep_full("vol_15>=5k AND n_bars_15>=8", lambda r: g(r, "vol_15") >= 5000 and g(r, "n_bars_15") >= 8)
sweep_full("vol_15>=25k AND n_bars_15>=8", lambda r: g(r, "vol_15") >= 25000 and g(r, "n_bars_15") >= 8)
sweep_full("vol_15>=5k OR n_bars_15>=14", lambda r: g(r, "vol_15") >= 5000 or g(r, "n_bars_15") >= 14)
print("\n-- stage-1 (free from new_pools listing, age ~2-5min) --")
sweep_full("reserve0>=5k", lambda r: g(r, "reserve0") >= 5000)
sweep_full("reserve0>=10k", lambda r: g(r, "reserve0") >= 10000)
sweep_full("reserve0>=20k", lambda r: g(r, "reserve0") >= 20000)
sweep_full("reserve0>=25k", lambda r: g(r, "reserve0") >= 25000)
sweep_full("reserve0>=10k OR vol_h1_seen>=2k", lambda r: g(r, "reserve0") >= 10000 or g(r, "vol_h1_seen") >= 2000)
sweep_full("reserve0>=20k OR vol_h1_seen>=5k", lambda r: g(r, "reserve0") >= 20000 or g(r, "vol_h1_seen") >= 5000)
print("(B lacks reserve0/vol_h1_seen — listing snapshot not recorded for recorder pools)")

print("\n== split stability of headline rules (w1 vs w23), FULL-floor label ==")
for wname in ("w1", "w23"):
    part = [r for r in A if r["window"] == wname]
    pf = [r for r in part if r["traction_full"]]
    for nm, fn in (("n_bars_15>=8", lambda r: g(r, "n_bars_15") >= 8),
                   ("vol_15>=5k&bars>=8", lambda r: g(r, "vol_15") >= 5000 and g(r, "n_bars_15") >= 8),
                   ("reserve0>=20k", lambda r: g(r, "reserve0") >= 20000)):
        sel = [r for r in part if fn(r)]
        tp = sum(1 for r in sel if r["traction_full"])
        print("  [%s n=%d posF=%d] %-20s pass %5.1f%% full-recall %d/%d prec %4.1f%%"
              % (wname, len(part), len(pf), nm, 100.0 * len(sel) / len(part),
                 tp, len(pf), 100.0 * tp / len(sel) if sel else 0))

print("\n== time-to-loudness (minutes from pool creation to cum bar-vol >= X) ==")
def t_to(bars, created, x):
    cum = 0.0
    for b in bars:
        if b[0] < created - 120: continue
        cum += b[5]
        if cum >= x: return max(0.0, (b[0] - created) / 60.0)
    return None

for tag, rows, bdir in (("A full-floor positives", [r for r in A if r["traction_full"]], "_gt_bars"),
                        ("B known-traction", Bg, "_gt_bars_b")):
    t1, t5 = [], []
    for r in rows:
        f = os.path.join(RIP, bdir, r["addr"][:12] + ".json")
        if not os.path.exists(f): continue
        bars = json.load(open(f))
        created = r.get("created") or r.get("created_ep")
        a = t_to(bars, created, 1000); b_ = t_to(bars, created, 5000)
        if a is not None: t1.append(a)
        if b_ is not None: t5.append(b_)
    print("  %s (n=%d): med t->$1k %.1fm ; med t->$5k %.1fm ; p90 t->$5k %.1fm"
          % (tag, len(rows), st.median(t1), st.median(t5),
             sorted(t5)[int(0.9 * len(t5))] if t5 else -1))

print("\n== dead-screen assumption risk ==")
withbars = [r for r in A if r["have_bars"]]
lowvol = [r for r in withbars if g(r, "vol_life") < 1000]
risk = sum(1 for r in lowvol if g(r, "n_bars_15") >= 8)
print("  pools WITH bars and vol_life<$1k: %d ; of those n_bars_15>=8: %d "
      "(rate %.1f%%) -> expected hidden passers among 134 no-bar pools: ~%.1f"
      % (len(lowvol), risk, 100.0 * risk / len(lowvol) if lowvol else 0,
         134 * risk / len(lowvol) if lowvol else 0))

print("\n== B age at first discovery (recorder) vs birth-filter timing ==")
ages = sorted(r["age_h_at_event"] for r in Bg)
print("  age@first-floor-event: med %.2fh ; p25 %.2fh ; p75 %.2fh ; min %.2fh"
      % (st.median(ages), ages[len(ages)//4], ages[3*len(ages)//4], ages[0]))

# overlap A cohort vs B recorder pools
aset = set(r["addr"] for r in A)
print("\noverlap A-cohort vs B-recall-set: %d" % sum(1 for r in B if r["addr"] in aset))
