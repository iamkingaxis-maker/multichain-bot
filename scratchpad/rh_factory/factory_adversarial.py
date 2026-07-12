"""Adversarial honesty pass over the factory sweep survivors (mine #4).

For each top survivor cell:
1. NEIGHBORHOOD: perturb every numeric cut one notch each way (dip edges
   +/-3pp, vol floor x0.5/x2, demand +/-50%, refine thresholds) and re-grade.
   A real edge's neighbors stay green (tokmed_ex2>0 in all 4 halves even if
   n dips below the bar); a lone spike dies. Report pass fraction.
2. STALE STRESS: trips resolved "stale" (pool went silent; exit booked at the
   last observed px >=300s after entry) assume a fill nobody guaranteed.
   Re-grade with every stale trip's ladder ret forced to -90% (unsellable)
   AND with stale trips dropped. Survivor must stay tokmed_ex2-green 4/4 in
   the DROP variant; the -90 variant is reported (it is the rug-worst-case).
3. Prints the survivorship/gap statement inputs (res mix, stale share).

Usage: python factory_adversarial.py   (after factory_sweep.py)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import factory_sweep as fs  # noqa: E402

OUT = fs.OUT


def parametric_select(rows, age_lo, age_hi, dip_lo, dip_hi, vol_min,
                      b30_min, ratio, extra=None):
    out = []
    for t in rows:
        a = t["age_h"]
        if a is None or not (age_lo <= a < age_hi):
            continue
        if not (dip_lo <= t["dip"] <= dip_hi):
            continue
        if t["cum_eth"] < vol_min:
            continue
        if t["b30"] < b30_min:
            continue
        if ratio is not None and not (t["b30"] > ratio * t["s30"]):
            continue
        if extra and not extra(t):
            continue
        out.append(t)
    return out


AGE_EDGES = {"u10m": (0.0, 1 / 6), "u1h": (0.0, 1.0), "1-6h": (1.0, 6.0),
             "6-24h": (6.0, 24.0), ">24h": (24.0, 1e9)}
DIP_EDGES = {"mod": (-25.0, -6.0), "sh": (-12.0, -6.0),
             "deep": (-1e9, -12.0), "vdeep": (-1e9, -25.0)}
VOL_MIN = {"v.3": 0.3, "v3": 3.0, "v10": 10.0}
DEM = {"d25": (0.015, None), "d50n": (0.03, 1.0), "d50x2": (0.03, 2.0)}
REFINE_PARAM = {   # refine name -> (param builder, notches)
    "arc<=1000": lambda v: (lambda t: t["arc"] is not None and t["arc"] <= v),
    "arc<=300":  lambda v: (lambda t: t["arc"] is not None and t["arc"] <= v),
    "popret":    lambda v: (lambda t: t["pop_ago"] is not None
                            and t["pop_ago"] <= v),
    "nopop":     lambda v: (lambda t: t["pop_ago"] is None
                            or t["pop_ago"] > v),
    "nb30>=3":   lambda v: (lambda t: t["nb30"] >= v),
    "h02-10":    None,   # hour cells: no numeric notch, tested as-is
    "h13-22":    None,
    "athdd>=-40": lambda v: (lambda t: t["athdd"] is not None
                             and t["athdd"] >= v),
}
REFINE_BASE = {"arc<=1000": 1000.0, "arc<=300": 300.0, "popret": 1800.0,
               "nopop": 1800.0, "nb30>=3": 3, "athdd>=-40": -40.0}
REFINE_NOTCH = {"arc<=1000": (700.0, 1500.0), "arc<=300": (200.0, 450.0),
                "popret": (1200.0, 2700.0), "nopop": (1200.0, 2700.0),
                "nb30>=3": (2, 4), "athdd>=-40": (-30.0, -55.0)}


def green_4(rows, exit_cls):
    """Direction check: tokmed_ex2>0 and cat<=0.05 in all 4 halves (n bar
    relaxed — neighborhood cells may be thinner; needs >=8 pools/half)."""
    for hk, hv in fs.HALVES:
        sub = [t for t in rows if t[hk] == hv]
        if not sub:
            return False
        st = fs.cell_stats(sub, exit_cls)
        if (st["pools"] < 8 or st["tokmed_ex2"] is None
                or st["tokmed_ex2"] <= 0 or st["cat"] > 0.05):
            return False
    return True


def neighborhood(surv, trips):
    age_lo, age_hi = AGE_EDGES[surv["age"]]
    dip_lo, dip_hi = DIP_EDGES[surv["dip"]]
    vol = VOL_MIN[surv["vol"]]
    b30, ratio = DEM[surv["dem"]]
    rname = surv.get("refine")
    rextra = None
    if rname:
        builder = REFINE_PARAM.get(rname)
        if builder is None:   # hour cell — keep as-is on all neighbors
            rextra = fs.REFINES[rname]
        else:
            rextra = builder(REFINE_BASE[rname])

    def base(dl=dip_lo, dh=dip_hi, v=vol, b=b30, ex=rextra):
        return parametric_select(trips, age_lo, age_hi, dl, dh, v, b,
                                 ratio, ex)

    nbrs = []
    if dip_lo > -1e8:
        nbrs += [("dip_lo-3", base(dl=dip_lo - 3)),
                 ("dip_lo+3", base(dl=dip_lo + 3))]
    nbrs += [("dip_hi-3", base(dh=dip_hi - 3)),
             ("dip_hi+3", base(dh=min(dip_hi + 3, -3.0)))]
    nbrs += [("vol_x0.5", base(v=vol * 0.5)), ("vol_x2", base(v=vol * 2))]
    nbrs += [("b30_x0.5", base(b=b30 * 0.5)), ("b30_x1.5", base(b=b30 * 1.5))]
    if rname and REFINE_PARAM.get(rname) is not None:
        lo, hi = REFINE_NOTCH[rname]
        builder = REFINE_PARAM[rname]
        nbrs += [(f"{rname}@{lo}", base(ex=builder(lo))),
                 (f"{rname}@{hi}", base(ex=builder(hi)))]
    results = []
    for name, rows in nbrs:
        ok = green_4(rows, surv["exit"])
        results.append((name, len(rows), ok))
    n_ok = sum(1 for _, _, ok in results if ok)
    return results, n_ok, len(results)


def stale_stress(surv, trips):
    age_lo, age_hi = AGE_EDGES[surv["age"]]
    dip_lo, dip_hi = DIP_EDGES[surv["dip"]]
    b30, ratio = DEM[surv["dem"]]
    rname = surv.get("refine")
    rextra = None
    if rname:
        builder = REFINE_PARAM.get(rname)
        rextra = (fs.REFINES[rname] if builder is None
                  else builder(REFINE_BASE[rname]))
    rows = parametric_select(trips, age_lo, age_hi, dip_lo, dip_hi,
                             VOL_MIN[surv["vol"]], b30, ratio, rextra)
    ex = surv["exit"]
    # dead pools are already BOOKED at -90 in the mine (v2); the remaining
    # unknowable is stale_end (stream ended inside the trip's horizon).
    # Variant A: drop them. Variant B: force them to -90 (worst case).
    drop = [t for t in rows if t["res"] != "stale_end"]
    forced = []
    for t in rows:
        if t["res"] == "stale_end":
            t2 = dict(t)
            t2[ex] = {"ret": -90.0, "hold": t[ex]["hold"], "legs": 1}
            forced.append(t2)
        else:
            forced.append(t)
    outs = {}
    for name, rs in (("base", rows), ("drop_staleend", drop),
                     ("staleend=-90", forced)):
        np, ph, ov = fs.grade_cell(rs, ex)
        outs[name] = {"pass_halves": np, "overall": ov}
    return outs


def main():
    fs.trips.extend(fs.load_trips())
    trips = fs.trips
    res = json.load(open(os.path.join(OUT, "sweep_results.json")))
    survivors = res["survivors"]
    if len(sys.argv) > 1:      # explicit cell names to audit
        want = set(sys.argv[1:])
        survivors = [s for s in survivors if s["cell"] in want]
        print(f"[adv] auditing {len(survivors)} named cells")
    report = []
    for surv in survivors[:12]:
        print(f"\n=== {surv['cell']} ===")
        nb, n_ok, n_tot = neighborhood(surv, trips)
        for name, n, ok in nb:
            print(f"  nbr {name:16s} n={n:5d} {'GREEN' if ok else 'red'}")
        ss = stale_stress(surv, trips)
        for name, o in ss.items():
            ov = o["overall"]
            print(f"  stale[{name:10s}] pass={o['pass_halves']}/4 "
                  f"tokmed_ex2=${ov['tokmed_ex2']:+.2f} "
                  f"cat={ov['cat']*100:.1f}% n={ov['n']}")
        report.append({"cell": surv["cell"],
                       "nbr_green": f"{n_ok}/{n_tot}",
                       "nbrs": [(nm, n, ok) for nm, n, ok in nb],
                       "stale": {k: v for k, v in ss.items()}})
    json.dump(report, open(os.path.join(OUT, "adversarial.json"), "w"),
              indent=1)
    print("\nwrote adversarial.json")


if __name__ == "__main__":
    main()
