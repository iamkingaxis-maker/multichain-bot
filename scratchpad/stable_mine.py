"""SOL young-lane STABILITY (dispersion-minimization) mine — 2026-07-13.

Goal: find the EXIT ladder that MINIMIZES per-trip return STD while keeping mean>=0,
applied to the young_rt/absorb entry population. Stability = low std + non-neg mean +
consistent across 4 OOS halves, NOT highest mean (fat-tail lottery).

Data: scratchpad/sol_selection/_trips.json (955 young-lane trips, 07-02..07-12; scrub
already applied upstream — verified 0 ret>0&hold<10). Sim from (peak, mae, ret, hold).
Model = blessed cap_oos_replay.py ladder, extended with variable t1/f1/stop/floor + min-hold.

Entry population: rt+absorb families (the least-bad entries; the 3 deliverables clone the
young_rt_paper entry). rt-only reported as robustness.

MANDATES: ex-top-2 token-median; SCRUB (already applied); 4-half OOS (W1/W2 x odd/even),
per-half reported; catastrophic = ret < -20 (bar <=5%); fat-tail trap (median/std must hold).
"""
import json, statistics as st
from collections import defaultdict

T = json.load(open("scratchpad/sol_selection/_trips.json", encoding="utf-8"))

RT_ABSORB = {"badday_young_rt", "badday_young_rt_paper",
             "badday_young_absorb", "badday_young_absorb_live"}
RT_ONLY = {"badday_young_rt", "badday_young_rt_paper"}
VSNAP = {"badday_young_vsnap_ab"}


def load(bots):
    rows = []
    for r in T:
        if r["bot"] not in bots:
            continue
        ret, hold, peak, mae = r.get("ret"), r.get("hold"), r.get("peak"), r.get("mae")
        tok = r.get("token") or r.get("address")
        if ret is None or hold is None or tok is None:
            continue
        # SCRUB (already applied upstream, enforce anyway)
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


def ladder(t, t1=6.0, f1=0.75, t2=12.0, f2=0.25, stop=-12.0, trail_pp=2.0, rem_floor=None):
    """General partial-sell ladder simulated from (peak M, mae m, ret live).
    rem_floor: if set (e.g. 0.0 breakeven-lock), reached-TP1 remainder floors here vs booking stop."""
    M, m, live = t["peak"], t["mae"], t["ret"]
    if M < t1:                                   # never reached TP1
        if m is not None and m <= stop:
            return stop
        return live
    proceeds = f1 * t1                            # bank f1 at TP1
    r1 = 1.0 - f1
    if r1 <= 1e-9:
        return proceeds                          # full-out at TP1
    if M < t2:                                    # reached TP1, not TP2
        if m is not None and m <= stop:
            rem = rem_floor if rem_floor is not None else stop
        else:
            rem = max(0.0, M - trail_pp)
        return proceeds + r1 * rem
    f2e = max(0.0, min(f2, r1))                   # reached TP2
    proceeds += f2e * t2
    rem_frac = r1 - f2e
    if rem_frac <= 1e-9:
        return proceeds
    if m is not None and m <= stop:
        rem = rem_floor if rem_floor is not None else stop
    else:
        rem = max(0.0, M - trail_pp)
    return proceeds + rem_frac * rem


def build_minhold(rows):
    """Conservative min-hold reprice constants, computed ON THIS population."""
    held = [t["ret"] for t in rows if 120 <= t["hold"] < 300]
    held_med = st.median(held) if held else 0.0
    bytok = defaultdict(lambda: {"cut": [], "held": []})
    for t in rows:
        if t["hold"] < 120 and t["ret"] < 0:
            bytok[t["tok"]]["cut"].append(t["ret"])
        if t["hold"] >= 120:
            bytok[t["tok"]]["held"].append(t["ret"])
    union = [(st.median(v["cut"]), st.median(v["held"]))
             for v in bytok.values() if v["cut"] and v["held"]]
    improv = st.median([h - c for c, h in union]) if union else 0.0
    return held_med, improv


def is_panic(t):
    return (t["hold"] < 120 and t["ret"] < 0 and t["peak"] < 6
            and (t["mae"] is None or t["mae"] > -25))


def make_valfn(rows, min_hold=False, **lad):
    held_med, improv = build_minhold(rows)

    def valfn(t):
        if min_hold and is_panic(t):
            return min(held_med, t["ret"] + improv)   # conservative reprice
        return ladder(t, **lad)
    return valfn


def pstd(v):
    return st.pstdev(v) if len(v) > 1 else 0.0


def report(name, rows, valfn, base_std=None):
    vals = [(t, valfn(t)) for t in rows]
    v = [x for _, x in vals]
    ex2_all = tokmed_ex2([(t["tok"], x) for t, x in vals])
    mean, med, std = st.mean(v), st.median(v), pstd(v)
    dnstd = pstd([min(x, 0) for x in v])           # downside deviation
    wr = 100 * sum(1 for x in v if x > 0) / len(v)
    cat = 100 * sum(1 for x in v if x < -20) / len(v)
    # per-half: ex2 + mean + std
    hstats = {}
    for tag in ("W1", "W2", "odd", "even"):
        sub = [(t, x) for t, x in vals if tag in half_tags(t["day"])]
        sv = [x for _, x in sub]
        hstats[tag] = (tokmed_ex2([(t["tok"], x) for t, x in sub]) if sub else float("nan"),
                       st.mean(sv) if sv else float("nan"),
                       pstd(sv) if sv else float("nan"))
    stdcut = "" if base_std is None else "  cut=%+5.1f%%" % (100 * (1 - std / base_std))
    green_halves = sum(1 for tag in ("W1", "W2", "odd", "even")
                       if not (hstats[tag][0] != hstats[tag][0]) and hstats[tag][0] >= -0.001)
    print("%-30s mean=%+6.2f med=%+6.2f STD=%6.2f%s dnstd=%5.2f ex2=%+6.2f wr=%2.0f cat=%.1f  halfEx2[W1=%+.1f W2=%+.1f o=%+.1f e=%+.1f]%d/4grn"
          % (name, mean, med, std, stdcut, dnstd, ex2_all, wr, cat,
             hstats["W1"][0], hstats["W2"][0], hstats["odd"][0], hstats["even"][0], green_halves))
    return {"name": name, "mean": mean, "med": med, "std": std, "dnstd": dnstd,
            "ex2": ex2_all, "wr": wr, "cat": cat, "halves": hstats, "green_halves": green_halves}


# ============================ RUN ============================
for popname, bots in (("RT+ABSORB (n core)", RT_ABSORB),):
    rows = load(bots)
    print("=" * 140)
    print("POPULATION: %s  n=%d trips  distinct-tok=%d" % (popname, len(rows), len(set(t["tok"] for t in rows))))
    raw = [t["ret"] for t in rows]
    print("RAW realized: mean=%+.2f med=%+.2f STD=%.2f  cat(<-20)=%.1f%%  (unmodeled realized ret)" %
          (st.mean(raw), st.median(raw), pstd(raw), 100 * sum(1 for x in raw if x < -20) / len(raw)))
    print("-" * 140)

    # BASELINE control ladder (the young_rt exit)
    base = report("CONTROL 6/.75 12/.25 -12 2pp", rows, make_valfn(rows, t1=6, f1=0.75))
    B = base["std"]
    print("-" * 140)
    print("[minhold + downside-cap family]")
    report("minhold only (deployed)", rows, make_valfn(rows, min_hold=True, t1=6, f1=0.75), B)
    report("minhold +BElock(rem0)", rows, make_valfn(rows, min_hold=True, t1=6, f1=0.75, rem_floor=0.0), B)
    report("minhold +stop-10", rows, make_valfn(rows, min_hold=True, t1=6, f1=0.75, stop=-10.0), B)
    report("minhold +stop-10 +BElock", rows, make_valfn(rows, min_hold=True, t1=6, f1=0.75, stop=-10.0, rem_floor=0.0), B)
    print("-" * 140)
    print("[TIGHT-BANK consistent-TP family — truncate right tail, offset w/ minhold+cap]")
    for t1, f1 in ((6, 0.85), (5, 0.85), (5, 0.90), (4, 0.90), (5, 1.0), (4, 1.0)):
        for stop in (-12.0, -10.0):
            report("bank %g@+%g stop%g +mh+BE" % (f1, t1, stop), rows,
                   make_valfn(rows, min_hold=True, t1=t1, f1=f1, t2=12, f2=max(0.0, 1 - f1),
                              stop=stop, rem_floor=0.0), B)
    print("-" * 140)
    print("[deployed capture_ab / barbell reference]")
    report("capture_ab 6/.6 12/.25 mb.3@3pp BE", rows,
           make_valfn(rows, min_hold=True, t1=6, f1=0.6, t2=12, f2=0.25, trail_pp=3.0, rem_floor=0.0), B)
