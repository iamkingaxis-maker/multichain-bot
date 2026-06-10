"""Young-probe family mine (2026-06-10): win/loss separators on the 81 closes
of young_probe_light/candidate (74% WR, +$167) -> clone-variant candidates.
Same recipe as the pond mine: numeric entry_meta separators, time-split halves,
keep only directionally-consistent separators with usable margins."""
import json, sys, collections, statistics
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

tr = json.load(open("_trades_cache.json"))
bb = collections.defaultdict(list)
for t in tr:
    if t.get("type") == "buy" and (t.get("bot_id") or "").startswith("young_probe"):
        bb[((t.get("pair_address") or t.get("address") or "").lower(), t["bot_id"])].append(t)
for k in bb:
    bb[k].sort(key=lambda b: b.get("time", ""))

rows = []
for t in tr:
    if t.get("type") != "sell" or not (t.get("bot_id") or "").startswith("young_probe"):
        continue
    if "cancelled" in (t.get("reason") or "").lower():
        continue
    k = ((t.get("pair_address") or t.get("address") or "").lower(), t["bot_id"])
    c = [b for b in bb.get(k, []) if (b.get("time") or "") < (t.get("time") or "")]
    if not c:
        continue
    em = c[-1].get("entry_meta") or {}
    rows.append((t.get("time", ""), float(t.get("pnl") or 0) > 0,
                 float(t.get("pnl") or 0), em, t.get("address") or t.get("token")))
rows.sort(key=lambda r: r[0])
half = len(rows) // 2
train, test = rows[:half], rows[half:]
print(f"rows={len(rows)} train={len(train)} test={len(test)} "
      f"| trainWR={sum(1 for r in train if r[1])/len(train):.0%} "
      f"testWR={sum(1 for r in test if r[1])/len(test):.0%}")

# numeric features present on >=70% of rows
feat_counts = collections.Counter()
for _, _, _, em, _ in rows:
    for kk, v in em.items():
        if isinstance(v, (int, float)):
            feat_counts[kk] += 1
feats = [f for f, n in feat_counts.items() if n >= 0.7 * len(rows)]
print(f"features tested: {len(feats)}")

def stats(data, f, side, thr):
    sel = [(w, u, tok) for _, w, u, em, tok in data
           if isinstance(em.get(f), (int, float))
           and ((em[f] >= thr) if side == ">=" else (em[f] <= thr))]
    if len(sel) < 12:
        return None
    wr = sum(1 for w, _, _ in sel if w) / len(sel)
    return wr, statistics.mean(u for _, u, _ in sel), len(sel), len({t for _, _, t in sel})

out = []
for f in feats:
    vals_w = [em[f] for _, w, _, em, _ in train if w and isinstance(em.get(f), (int, float))]
    vals_l = [em[f] for _, w, _, em, _ in train if not w and isinstance(em.get(f), (int, float))]
    if len(vals_w) < 10 or len(vals_l) < 6:
        continue
    mw, ml = statistics.median(vals_w), statistics.median(vals_l)
    if mw == ml:
        continue
    side = ">=" if mw > ml else "<="
    thr = round((mw + ml) / 2, 4)
    tr_s = stats(train, f, side, thr)
    te_s = stats(test, f, side, thr)
    if not tr_s or not te_s:
        continue
    base_tr = sum(1 for r in train if r[1]) / len(train)
    base_te = sum(1 for r in test if r[1]) / len(test)
    if tr_s[0] >= base_tr + 0.06 and te_s[0] >= base_te + 0.04 and te_s[1] > 0:
        out.append((f, side, thr, tr_s, te_s))

out.sort(key=lambda x: -x[4][0])
print(f"\nheld-out separators (train +6pp, test +4pp, test $+):")
print(f"{'feature':38s}{'cond':>12s}{'trWR':>6s}{'teWR':>6s}{'te$/tr':>8s}{'teN':>5s}{'tok':>4s}")
for f, side, thr, tr_s, te_s in out[:18]:
    print(f"  {f:36s}{side}{thr:>10} {tr_s[0]*100:4.0f}% {te_s[0]*100:4.0f}% "
          f"{te_s[1]:+7.2f} {te_s[2]:4d} {te_s[3]:3d}")

# combos of the top separators
print("\ncombos (2-feature):")
import itertools
top = out[:8]
for (f1, s1, t1, _, _), (f2, s2, t2, _, _) in itertools.combinations(top, 2):
    def csel(data):
        sel = [(w, u, tok) for _, w, u, em, tok in data
               if isinstance(em.get(f1), (int, float)) and isinstance(em.get(f2), (int, float))
               and ((em[f1] >= t1) if s1 == ">=" else (em[f1] <= t1))
               and ((em[f2] >= t2) if s2 == ">=" else (em[f2] <= t2))]
        if len(sel) < 10:
            return None
        wr = sum(1 for w, _, _ in sel if w) / len(sel)
        return wr, statistics.mean(u for _, u, _ in sel), len(sel), len({t for _, _, t in sel})
    a, b = csel(train), csel(test)
    if a and b and a[0] >= 0.85 and b[0] >= 0.80 and b[1] > 0:
        print(f"  {f1}{s1}{t1} + {f2}{s2}{t2}: tr {a[0]:.0%} | te {b[0]:.0%} "
              f"${b[1]:+.2f}/tr n={b[2]} tok={b[3]}")
