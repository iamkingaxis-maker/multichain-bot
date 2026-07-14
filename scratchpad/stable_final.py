"""Finalize 3 STABLE configs + per-half stability detail + rt-only/vsnap robustness.
Reuses the blessed ladder model from stable_mine.py. Each candidate maps to EXACT config knobs.
"""
import json, statistics as st
from collections import defaultdict

T = json.load(open("scratchpad/sol_selection/_trips.json", encoding="utf-8"))
RT_ABSORB = {"badday_young_rt", "badday_young_rt_paper", "badday_young_absorb", "badday_young_absorb_live"}
RT_ONLY = {"badday_young_rt", "badday_young_rt_paper"}
VSNAP = {"badday_young_vsnap_ab"}
ALL_LEASTBAD = RT_ABSORB | VSNAP


def load(bots):
    rows = []
    for r in T:
        if r["bot"] not in bots:
            continue
        ret, hold, peak, mae = r.get("ret"), r.get("hold"), r.get("peak"), r.get("mae")
        tok = r.get("token") or r.get("address")
        if ret is None or hold is None or tok is None:
            continue
        if ret > 0 and hold < 10:
            continue
        rows.append({"tok": tok, "ret": float(ret), "hold": float(hold),
                     "peak": float(peak) if peak is not None else 0.0,
                     "mae": float(mae) if mae is not None else None,
                     "day": (r.get("time") or "")[:10]})
    return rows


def half_tags(day):
    try:
        dom = int(day[8:10])
    except Exception:
        return []
    return [("W1" if day <= "2026-07-06" else "W2"), ("odd" if dom % 2 else "even")]


def tokmed_ex2(pairs):
    bytok = defaultdict(list)
    for tok, v in pairs:
        bytok[tok].append(v)
    meds = sorted(((k, st.median(v), len(v)) for k, v in bytok.items()), key=lambda x: -x[2])
    ex2 = [m for _, m, _ in meds[2:]]
    return st.median(ex2) if ex2 else float("nan")


def pstd(v):
    return st.pstdev(v) if len(v) > 1 else 0.0


def ladder(t, t1=6.0, f1=0.75, t2=12.0, f2=0.25, stop=-12.0, trail_pp=2.0,
           mb_frac=0.0, mb_trail=3.0, mb_floor=0.0):
    """Faithful engine map: TP1 sells f1@t1; TP2 sells f2@t2 (only if peak>=t2);
    moonbag mb_frac rides ONLY after TP2 (+t2) reached, BE-floored at mb_floor, trailed mb_trail.
    Pre-TP2 remainder rides trail_pp; hard stop at `stop` (MAE-gated)."""
    M, m, live = t["peak"], t["mae"], t["ret"]
    if M < t1:
        if m is not None and m <= stop:
            return stop
        return live
    proceeds = f1 * t1
    r1 = 1.0 - f1
    if r1 <= 1e-9:
        return proceeds
    if M < t2:                                   # reached TP1, not TP2: whole remainder trails
        if m is not None and m <= stop:
            rem = stop
        else:
            rem = max(0.0, M - trail_pp)
        return proceeds + r1 * rem
    # reached TP2: sell f2 of original at t2; moonbag mb_frac rides on; middle piece trails
    f2e = max(0.0, min(f2, r1 - mb_frac))
    proceeds += f2e * t2
    rem_frac = r1 - f2e - mb_frac
    if rem_frac > 1e-9:                           # non-moonbag remainder trails (BE-safe post-TP2)
        proceeds += rem_frac * (max(mb_floor, M - trail_pp) if (m is not None and m <= stop) else max(0.0, M - trail_pp))
    if mb_frac > 1e-9:                            # BE-locked moonbag rides the runner
        mb = max(mb_floor, M - mb_trail)
        proceeds += mb_frac * mb
    return proceeds


def build_minhold(rows):
    held = [t["ret"] for t in rows if 120 <= t["hold"] < 300]
    held_med = st.median(held) if held else 0.0
    bytok = defaultdict(lambda: {"cut": [], "held": []})
    for t in rows:
        if t["hold"] < 120 and t["ret"] < 0:
            bytok[t["tok"]]["cut"].append(t["ret"])
        if t["hold"] >= 120:
            bytok[t["tok"]]["held"].append(t["ret"])
    union = [(st.median(v["cut"]), st.median(v["held"])) for v in bytok.values() if v["cut"] and v["held"]]
    improv = st.median([h - c for c, h in union]) if union else 0.0
    return held_med, improv


def is_panic(t):
    return (t["hold"] < 120 and t["ret"] < 0 and t["peak"] < 6 and (t["mae"] is None or t["mae"] > -25))


def make_valfn(rows, min_hold=True, **lad):
    held_med, improv = build_minhold(rows)

    def valfn(t):
        if min_hold and is_panic(t):
            return min(held_med, t["ret"] + improv)
        return ladder(t, **lad)
    return valfn


def full(name, rows, valfn, base_std=None):
    vals = [(t, valfn(t)) for t in rows]
    v = [x for _, x in vals]
    mean, med, std = st.mean(v), st.median(v), pstd(v)
    ex2 = tokmed_ex2([(t["tok"], x) for t, x in vals])
    wr = 100 * sum(1 for x in v if x > 0) / len(v)
    cat = 100 * sum(1 for x in v if x < -20) / len(v)
    dnstd = pstd([min(x, 0) for x in v])
    halves = {}
    for tag in ("W1", "W2", "odd", "even"):
        sub = [(t, x) for t, x in vals if tag in half_tags(t["day"])]
        sv = [x for _, x in sub]
        halves[tag] = dict(ex2=tokmed_ex2([(t["tok"], x) for t, x in sub]) if sub else float("nan"),
                           mean=st.mean(sv) if sv else float("nan"), std=pstd(sv) if sv else float("nan"), n=len(sv))
    grn = sum(1 for tag in halves if halves[tag]["ex2"] == halves[tag]["ex2"] and halves[tag]["ex2"] >= -0.001)
    cutd = "" if base_std is None else " cut%+5.1f%%" % (100 * (1 - std / base_std))
    print("%-34s mean=%+5.2f med=%+5.2f STD=%5.2f%s ex2=%+5.2f wr=%2.0f cat=%.1f %d/4grn" %
          (name, mean, med, std, cutd, ex2, wr, cat, grn))
    return dict(name=name, mean=mean, med=med, std=std, ex2=ex2, wr=wr, cat=cat, dnstd=dnstd, halves=halves, grn=grn)


# ==== finalists ====
CANDS = [
    ("CONTROL (young_rt exit, no minhold)", dict(min_hold=False, t1=6, f1=0.75, t2=12, f2=0.25, stop=-12, trail_pp=2)),
    ("DEPLOYED young_rt (minhold only)",    dict(min_hold=True,  t1=6, f1=0.75, t2=12, f2=0.25, stop=-12, trail_pp=2)),
    ("stable1 bank.85@6 /.15@12 stop-12",   dict(min_hold=True,  t1=6, f1=0.85, t2=12, f2=0.15, stop=-12, trail_pp=2)),
    ("stable1+ .85@6 /mb.15 BE 3pp",        dict(min_hold=True,  t1=6, f1=0.85, t2=12, f2=0.0, stop=-12, trail_pp=2, mb_frac=0.15, mb_trail=3, mb_floor=0.0)),
    ("stable2 bank.90@5 /.10@12 stop-10",   dict(min_hold=True,  t1=5, f1=0.90, t2=12, f2=0.10, stop=-10, trail_pp=2)),
    ("stable3 full-scalp 1.0@4 stop-10",    dict(min_hold=True,  t1=4, f1=1.0,  t2=12, f2=0.0, stop=-10, trail_pp=2)),
    ("(x) full-scalp 1.0@3 stop-10",        dict(min_hold=True,  t1=3, f1=1.0,  t2=12, f2=0.0, stop=-10, trail_pp=2)),
    ("(x) capture_ab .6@6 mb.3 3pp (mean)", dict(min_hold=True,  t1=6, f1=0.6, t2=12, f2=0.25, stop=-12, trail_pp=2, mb_frac=0.3, mb_trail=3, mb_floor=0.0)),
]

for popname, bots in (("RT+ABSORB", RT_ABSORB), ("RT-ONLY", RT_ONLY), ("VSNAP", VSNAP), ("ALL-LEAST-BAD(rt+absorb+vsnap)", ALL_LEASTBAD)):
    rows = load(bots)
    print("=" * 120)
    print("POP %s  n=%d  tok=%d" % (popname, len(rows), len(set(t["tok"] for t in rows))))
    base = None
    res = {}
    for nm, kw in CANDS:
        r = full(nm, rows, make_valfn(rows, **kw), base)
        res[nm] = r
        if base is None:
            base = r["std"]

# per-half detail for the 3 finalists on RT+ABSORB
print("\n" + "=" * 120)
print("PER-HALF STABILITY DETAIL (RT+ABSORB) — mean/std/ex2 per OOS half")
rows = load(RT_ABSORB)
for nm, kw in CANDS:
    if not nm.startswith(("stable", "DEPLOYED", "CONTROL")):
        continue
    r = full(nm, rows, make_valfn(rows, **kw))
    for tag in ("W1", "W2", "odd", "even"):
        h = r["halves"][tag]
        print("      %-4s n=%3d  mean=%+6.2f  std=%6.2f  ex2=%+6.2f" % (tag, h["n"], h["mean"], h["std"], h["ex2"]))
