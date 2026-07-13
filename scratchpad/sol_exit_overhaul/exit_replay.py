"""SOL young-lane EXIT-ladder replay/ranking (2026-07-12).

REUSES scratchpad/sol_selection/_trips.json (955 realized young-lane trips, post-scrub,
07-02..07-12) with per-trip peak(MFE) / mae(MAE) / ret(realized under LIVE ladder) / hold.

METHOD (honest, from the codebase-blessed sol_deep_analysis.py pattern):
  - MFE(peak) is the max FAVORABLE excursion observed UP TO the live exit. It is
    TRUNCATED for grinders the live ladder sold early -> the RUNNER leg of any
    barbell/hot-tuned variant is a LOWER BOUND (a real runner could ride past where
    the live ladder sold). TP-harvest legs at targets <= the live TP1 (+6) are RELIABLE
    (if MFE>=T the +T print happened). So replay RANKS; forward paper CONFIRMS.
  - min_hold_floor is a LOSS-SIDE change on trips a soft cutter cut <120s. Their post-cut
    path is unobservable from summary tape, so min_hold is graded by (a) the same-token
    UNION count (robust, holds token fixed) and (b) a bounded cohort re-price to the
    120-300s conditional median (survivorship UPPER bound). Both labeled.

Metrics per variant: captured-pp (mean realized pp/trip = expectancy), median,
ex-top-2 token-median (robust; group by token, median per token, drop the 2 highest-count
tokens, median of the rest), WR, cat<=-25%, and the 4-half OOS ex2 (W1/W2 x odd/even).
All writes utf-8. Ranking only; no code path touched.
"""
import json
import statistics as st
from collections import defaultdict

TRIPS = json.load(open("scratchpad/sol_selection/_trips.json", encoding="utf-8"))


def num(x):
    try:
        return None if x is None else float(x)
    except Exception:
        return None


# ---- normalize trips ----
T = []
for r in TRIPS:
    ret = num(r.get("ret"))
    hold = num(r.get("hold"))
    peak = num(r.get("peak"))
    mae = num(r.get("mae"))
    tok = r.get("token") or r.get("address")
    day = (r.get("time") or "")[:10]
    if ret is None or hold is None or tok is None:
        continue
    T.append({"tok": tok, "ret": ret, "hold": hold,
              "peak": peak if peak is not None else 0.0,
              "mae": mae, "day": day})

print(f"trips: {len(T)}  tokens: {len(set(t['tok'] for t in T))}")


def half_tags(day):
    try:
        dom = int(day[8:10])
    except Exception:
        return []
    return [("W1" if day <= "2026-07-06" else "W2"), ("odd" if dom % 2 else "even")]


def tokmed_ex2(vals_by_trip):
    """vals_by_trip: list of (tok, value). ex-top-2 token-median."""
    bytok = defaultdict(list)
    for tok, v in vals_by_trip:
        bytok[tok].append(v)
    meds = sorted(((k, st.median(v), len(v)) for k, v in bytok.items()),
                  key=lambda x: -x[2])
    ex2 = [m for _, m, _ in meds[2:]]
    return (st.median(ex2) if ex2 else float("nan"), len(meds))


def summarize(name, vals):
    """vals: list of (trip, value)."""
    v = [x for _, x in vals]
    tok_vals = [(t["tok"], x) for t, x in vals]
    ex2, ntok = tokmed_ex2(tok_vals)
    wr = 100 * sum(1 for x in v if x > 0) / len(v)
    cat = 100 * sum(1 for x in v if x <= -25) / len(v)
    # 4-half OOS ex2
    halves = {}
    for tag in ("W1", "W2", "odd", "even"):
        sub = [(t["tok"], x) for t, x in vals if tag in half_tags(t["day"])]
        halves[tag] = tokmed_ex2(sub)[0] if sub else float("nan")
    minhalf = min(h for h in halves.values() if h == h)
    return {
        "name": name, "n": len(v), "mean": st.mean(v), "med": st.median(v),
        "ex2": ex2, "ntok": ntok, "wr": wr, "cat": cat,
        "halves": halves, "minhalf": minhalf,
        "green4": all(h > 0 for h in halves.values() if h == h),
    }


def prow(s):
    h = s["halves"]
    g4 = "GREEN4" if s["green4"] else "     -"
    print(f"{s['name']:26s} {s['mean']:+7.2f} {s['med']:+7.2f} {s['ex2']:+8.2f} "
          f"{s['minhalf']:+8.2f} {s['wr']:5.0f} {s['cat']:5.1f} {g4} "
          f"[{h['W1']:+.1f}/{h['W2']:+.1f}/{h['odd']:+.1f}/{h['even']:+.1f}]")


HDR = (f"{'variant':26s} {'mean':>7s} {'med':>7s} {'ex2':>8s} {'minhalf':>8s} "
       f"{'wr%':>5s} {'cat%':>5s} {'oos':>6s} [W1/W2/odd/even ex2]")


# =====================================================================
# 1. LADDER REPLAY (MFE-based; TP legs reliable, runner = lower bound)
# =====================================================================
def ladder(t, t1=6.0, f1=0.75, t2=12.0, f2=0.25, mb=0.0, mb_floor=0.0,
           mb_trail=None, stop=-12.0, trail_pp=2.0):
    """Estimate realized pp for one trip under a candidate ladder from MFE/MAE/live.

    Reliable where MFE reveals the harvest; runner leg (post-TP2 moonbag) is a
    LOWER bound (MFE truncated by the live exit). Stop applied when MAE<=stop and
    the position never reached TP1 (a real down-then-out); if it reached TP1 first
    the banked legs are locked and only the residual is at risk.
    """
    M = t["peak"]
    m = t["mae"]
    live = t["ret"]
    # Never reached TP1: pre-TP1 outcome.
    if M < t1:
        if m is not None and m <= stop:
            return stop
        return live  # soft-cutter / small-loss outcome (unchanged by this ladder)
    # Reached TP1: bank f1 @ t1.
    r1 = 1.0 - f1
    proceeds = f1 * t1
    if M < t2:
        # remainder rides; no TP2. If it later stopped, book stop on remainder;
        # else it trailed near the peak -> approximate remainder at max(0, M-trail_pp).
        if m is not None and m <= stop:
            return proceeds + r1 * stop
        return proceeds + r1 * max(0.0, M - trail_pp)
    # Reached TP2: bank the TP2 leg.
    if mb > 0:
        f2e = max(0.0, min(f2, r1 - mb))
        proceeds += f2e * t2
        runner = mb
        # moonbag rides: exits at max(floor, MFE - trail). LOWER bound.
        mb_exit = max(mb_floor, (M - mb_trail) if mb_trail is not None else mb_floor)
        proceeds += runner * mb_exit
        return proceeds
    else:
        f2e = max(0.0, min(f2, r1))
        proceeds += f2e * t2
        rem = r1 - f2e
        # remainder trails from peak
        proceeds += rem * max(0.0, M - trail_pp)
        return proceeds


VARIANTS = {
    "control(6/.75,12,2pp)":      dict(t1=6, f1=0.75, t2=12, f2=0.25, trail_pp=2.0, stop=-12),
    "barbell(6/.60,12,mb.30/12)": dict(t1=6, f1=0.60, t2=12, f2=0.25, mb=0.30,
                                       mb_floor=0.0, mb_trail=12.0, stop=-12),
    "hot_tuned(6/.5,20,4pp)":     dict(t1=6, f1=0.50, t2=20, f2=0.50, trail_pp=4.0, stop=-12),
    "hot_barbell(6/.5,18,mb.35/15)": dict(t1=6, f1=0.50, t2=18, f2=0.30, mb=0.35,
                                          mb_floor=0.0, mb_trail=15.0, stop=-12),
}

print("\n=== LADDER REPLAY (MFE-based; runner leg = LOWER bound, forward-confirm) ===")
print(HDR)
ladder_rows = {}
for name, kw in VARIANTS.items():
    vals = [(t, ladder(t, **kw)) for t in T]
    s = summarize(name, vals)
    ladder_rows[name] = s
    prow(s)

# captured-pp vs control
ctl = ladder_rows["control(6/.75,12,2pp)"]
print("\ncaptured-pp (mean) and ex2 vs control:")
for name, s in ladder_rows.items():
    print(f"  {name:28s} dmean={s['mean']-ctl['mean']:+.2f}pp  dex2={s['ex2']-ctl['ex2']:+.2f}pp")


# =====================================================================
# 2. BOUNCE DISTRIBUTION — do bounces run bigger than +12? (hot-tuned rationale)
# =====================================================================
print("\n=== BOUNCE DISTRIBUTION (MFE of trips that reached TP1 +6) ===")
reached = [t["peak"] for t in T if t["peak"] >= 6]
allpk = [t["peak"] for t in T]
print(f"trips reaching +6 (TP1): {len(reached)}/{len(T)} ({100*len(reached)/len(T):.0f}%)")
if reached:
    rs = sorted(reached)
    def pct(p): return rs[min(len(rs)-1, int(len(rs)*p))]
    print(f"  MFE|reached-TP1: med={st.median(reached):+.1f} p75={pct(.75):+.1f} "
          f"p90={pct(.90):+.1f} p95={pct(.95):+.1f} max={max(reached):+.1f}")
    for thr in (12, 18, 20, 30, 50):
        n = sum(1 for x in allpk if x >= thr)
        print(f"  trips reaching +{thr:>2}: {n:4d} ({100*n/len(T):.1f}% of all)")


# =====================================================================
# 3. MIN-HOLD FLOOR — same-token union (robust) + bounded cohort re-price
# =====================================================================
print("\n=== MIN-HOLD FLOOR (~120s), SOFT cutters gated pre-TP1 (rug tripwire stays) ===")
FLOOR = 120.0
# panic-cut cohort: cut <FLOOR, red, never reached TP1, not a real catastrophe (mae>-25)
panic = [t for t in T if t["hold"] < FLOOR and t["ret"] < 0 and t["peak"] < 6
         and (t["mae"] is None or t["mae"] > -25)]
held = [t for t in T if FLOOR <= t["hold"] < 300]  # the 120-300 sweet spot
print(f"panic-cut cohort (hold<120,red,peak<6,mae>-25): {len(panic)} trips "
      f"({100*len(panic)/len(T):.0f}% of vol), {len(set(t['tok'] for t in panic))} tokens")
print(f"  panic med ret={st.median([t['ret'] for t in panic]):+.1f}  "
      f"mean={st.mean([t['ret'] for t in panic]):+.1f}")
held_med = st.median([t["ret"] for t in held])
print(f"  120-300s held bucket: n={len(held)} med ret={held_med:+.1f} "
      f"WR={100*sum(1 for t in held if t['ret']>0)/len(held):.0f}%")

# same-token union: tokens with BOTH a <120 red cut AND a >=120 hold
bytok = defaultdict(lambda: {"cut": [], "held": []})
for t in T:
    if t["hold"] < FLOOR and t["ret"] < 0:
        bytok[t["tok"]]["cut"].append(t["ret"])
    if t["hold"] >= FLOOR:
        bytok[t["tok"]]["held"].append(t["ret"])
union = [(k, st.median(v["cut"]), st.median(v["held"]))
         for k, v in bytok.items() if v["cut"] and v["held"]]
if union:
    beat = sum(1 for _, c, h in union if h > c)
    improv = st.median([h - c for _, c, h in union])
    print(f"\nsame-token union (both a <120 cut AND a >=120 hold): {len(union)} tokens")
    print(f"  median cut ret={st.median([c for _,c,_ in union]):+.1f}  ->  "
          f"median held ret={st.median([h for _,_,h in union]):+.1f}")
    print(f"  holding beat cutting on {beat}/{len(union)} = {100*beat/len(union):.0f}% of tokens")
    print(f"  median improvement from holding = {improv:+.1f} pp")

# bounded cohort re-price: floored panic-cuts realize the conditional held median (UPPER bound)
print("\nmin-hold bounded replay (panic-cuts RE-PRICED to 120-300 held median; UPPER bound):")
for lbl, reprice in [("upper: reprice->+held_med", held_med),
                     ("conservative: cut+union_improv (capped +held_med)", None)]:
    vals = []
    for t in T:
        if t in panic:
            if reprice is not None:
                vals.append((t, reprice))
            else:
                vals.append((t, min(held_med, t["ret"] + (improv if union else 0))))
        else:
            vals.append((t, t["ret"]))
    s = summarize(lbl[:26], vals)
    prow(s)
# control (live realized) for reference on the SAME cohort framing
sctl = summarize("live-realized(all trips)", [(t, t["ret"]) for t in T])
print(HDR)
prow(sctl)

# =====================================================================
# 4. COMBINED: min-hold floor (loser cohort re-price) + barbell (winner tail)
# =====================================================================
print("\n=== COMBINED min_hold+barbell (median lift AND tail capture) ===")
print(HDR)
for lbl, reprice in [("combined UPPER", held_med),
                     ("combined CONSERVATIVE", None)]:
    vals = []
    for t in T:
        if t in panic:
            # min_hold re-prices the panic-cut loser cohort
            if reprice is not None:
                vals.append((t, reprice))
            else:
                vals.append((t, min(held_med, t["ret"] + (improv if union else 0))))
        else:
            # barbell captures the winner tail (lower bound), unchanged losers pass live
            vals.append((t, ladder(t, t1=6, f1=0.60, t2=12, f2=0.25, mb=0.30,
                                   mb_floor=0.0, mb_trail=12.0, stop=-12)))
    prow(summarize(lbl, vals))

print("\nDONE. Ranking only; runner legs are lower bounds; min_hold via union + bound.")
