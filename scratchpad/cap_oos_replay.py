"""OOS replay of the POST-TP1 REMAINDER BREAKEVEN-LOCK lever on trips.json (955, 07-02..07-12).

Cache legs (07-11..13) proved the leak: on winners the 25% remainder after TP1 round-trips to
-9..-13 (small winners) or catastrophically reverses (a few monsters). The lever: once TP1 has
banked 75% at +6, floor the remaining 25% at breakeven (never let it book below 0). This is the
opposite bet to a fat moonbag (moonbag_ab caught the most tail yet had the worst median).

Baseline `control` = the exit_replay.py ladder (blessed). `belock` = same, but the reached-TP1
remainder is floored at 0 instead of booking the stop. Reported: ex-top-2 token-median per 4
chrono halves (W1/W2 x odd/even), captured-pp mean, WR, cat. Additive combo with min-hold floor
(the only ex2 mover) also shown. Runner legs are LOWER bounds (MFE truncated); ranking only.
"""
import json, statistics as st
from collections import defaultdict

T = json.load(open("scratchpad/sol_selection/_trips.json", encoding="utf-8"))
rows = []
for r in T:
    ret, hold, peak, mae = r.get("ret"), r.get("hold"), r.get("peak"), r.get("mae")
    tok = r.get("token") or r.get("address")
    if ret is None or hold is None or tok is None:
        continue
    rows.append({"tok": tok, "ret": float(ret), "hold": float(hold),
                 "peak": float(peak) if peak is not None else 0.0,
                 "mae": float(mae) if mae is not None else None,
                 "day": (r.get("time") or "")[:10]})


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


def ladder(t, t1=6.0, f1=0.75, t2=12.0, f2=0.25, stop=-12.0, trail_pp=2.0,
           rem_floor=None, min_hold=None):
    """rem_floor: if set (e.g. 0.0), the reached-TP1 remainder floors here instead of booking stop.
    min_hold: if set, panic-cut losers (hold<min_hold, red, peak<t1, mae>-25) reprice to held-med."""
    M, m, live = t["peak"], t["mae"], t["ret"]
    if M < t1:
        # pre-TP1: min-hold reprice handled by caller; here book stop or live
        if m is not None and m <= stop:
            return stop
        return live
    r1 = 1.0 - f1
    proceeds = f1 * t1
    # remainder outcome when reached TP1
    if M < t2:
        if m is not None and m <= stop:
            rem = rem_floor if rem_floor is not None else stop
        else:
            rem = max(0.0, M - trail_pp)
        return proceeds + r1 * rem
    # reached TP2
    f2e = max(0.0, min(f2, r1))
    proceeds += f2e * t2
    rem_frac = r1 - f2e
    if m is not None and m <= stop:
        rem = rem_floor if rem_floor is not None else stop
    else:
        rem = max(0.0, M - trail_pp)
    return proceeds + rem_frac * rem


HELD = [t["ret"] for t in rows if 120 <= t["hold"] < 300]
HELD_MED = st.median(HELD)
# same-token union improvement (conservative min-hold)
bytok = defaultdict(lambda: {"cut": [], "held": []})
for t in rows:
    if t["hold"] < 120 and t["ret"] < 0:
        bytok[t["tok"]]["cut"].append(t["ret"])
    if t["hold"] >= 120:
        bytok[t["tok"]]["held"].append(t["ret"])
union = [(st.median(v["cut"]), st.median(v["held"])) for v in bytok.values() if v["cut"] and v["held"]]
IMPROV = st.median([h - c for c, h in union]) if union else 0.0


def is_panic(t):
    return (t["hold"] < 120 and t["ret"] < 0 and t["peak"] < 6
            and (t["mae"] is None or t["mae"] > -25))


def variant_val(t, rem_floor=None, min_hold=False):
    if min_hold and is_panic(t):
        return min(HELD_MED, t["ret"] + IMPROV)   # conservative reprice
    return ladder(t, rem_floor=rem_floor)


def report(name, valfn):
    vals = [(t, valfn(t)) for t in rows]
    v = [x for _, x in vals]
    ex2_all = tokmed_ex2([(t["tok"], x) for t, x in vals])
    halves = {}
    for tag in ("W1", "W2", "odd", "even"):
        sub = [(t["tok"], x) for t, x in vals if tag in half_tags(t["day"])]
        halves[tag] = tokmed_ex2(sub) if sub else float("nan")
    wr = 100 * sum(1 for x in v if x > 0) / len(v)
    cat = 100 * sum(1 for x in v if x <= -25) / len(v)
    print("%-26s mean=%+6.2f med=%+6.2f ex2=%+6.2f wr=%2.0f cat=%.1f  [W1=%+.2f W2=%+.2f odd=%+.2f even=%+.2f]"
          % (name, st.mean(v), st.median(v), ex2_all, wr, cat,
             halves["W1"], halves["W2"], halves["odd"], halves["even"]))
    return ex2_all, halves


print("n trips=%d  HELD_MED=%.2f  union_improv=%.2f" % (len(rows), HELD_MED, IMPROV))
print("\n=== POST-TP1 REMAINDER BREAKEVEN-LOCK (ex-top-2 token-median, 4-half OOS) ===")
c_ex2, c_h = report("control", lambda t: variant_val(t))
b_ex2, b_h = report("belock (rem_floor=0)", lambda t: variant_val(t, rem_floor=0.0))
print("  belock vs control per-half dex2: " +
      "  ".join("%s=%+.2f" % (k, b_h[k] - c_h[k]) for k in ("W1", "W2", "odd", "even")))
wins = sum(1 for k in ("W1", "W2", "odd", "even") if b_h[k] > c_h[k] + 1e-9)
print("  belock improves ex2 in %d/4 halves" % wins)

print("\n=== ADDITIVE COMBO: min-hold floor + belock (min-hold is the ex2 mover) ===")
mh_ex2, mh_h = report("minhold", lambda t: variant_val(t, min_hold=True))
mhb_ex2, mhb_h = report("minhold+belock", lambda t: variant_val(t, rem_floor=0.0, min_hold=True))
print("  (minhold+belock) vs (minhold) per-half dex2: " +
      "  ".join("%s=%+.2f" % (k, mhb_h[k] - mh_h[k]) for k in ("W1", "W2", "odd", "even")))
wins2 = sum(1 for k in ("W1", "W2", "odd", "even") if mhb_h[k] > mh_h[k] + 1e-9)
print("  belock-on-top-of-minhold improves ex2 in %d/4 halves" % wins2)

# capture-pp lift on the WINNER cohort (peak>=6) — the honest place belock acts
w = [t for t in rows if t["peak"] >= 6]
cw = [ladder(t) for t in w]
bw = [ladder(t, rem_floor=0.0) for t in w]
print("\nWINNER cohort (peak>=6, n=%d): control mean=%+.2f med=%+.2f | belock mean=%+.2f med=%+.2f | dmean=%+.2f"
      % (len(w), st.mean(cw), st.median(cw), st.mean(bw), st.median(bw), st.mean(bw) - st.mean(cw)))
# how many winners does belock touch (reached TP1 then mae<=stop)?
touched = [t for t in w if t["mae"] is not None and t["mae"] <= -12]
print("winners where remainder round-tripped to <=-12 (belock acts): %d (%.0f%% of winners)"
      % (len(touched), 100 * len(touched) / len(w)))
