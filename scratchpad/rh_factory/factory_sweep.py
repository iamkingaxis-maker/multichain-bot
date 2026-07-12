"""CONFIG-SPACE SWEEP over factory candidates (candidate factory mine #2/#3).

Reads rh_factory/candidates.jsonl.gz (rich-stamped loose dip candidates with
3 simulated exit-ladder outcomes, haircuts included) and grades every cell of
a structured entry-config grid against the PHASE-1 BAR applied per half of
the four-half discipline (chrono W1 07-01..05 / W2 07-06..11 x odd/even
day-of-month):

  PASS(half) = n>=20 DISTINCT pools AND token-median net (ex-top-2 pools) > 0
               AND catastrophic rate (trip ret <= -50%) <= 1/20
  A cell SURVIVES only at 4/4 halves + >=5 distinct days overall.

Grid axes (primary product):
  age band x dip band x volume floor (cum_eth, the liq proxy) x demand x exit
Refinement axes applied to primary survivors + top near-misses:
  arc cap, pop-recency (pop-retrace family = pop_ago<=1800), hour cells,
  demand breadth (nb30).

$ terms: every trip = $25 notional; trip net USD = ret_pct/100 * 25.
Output: rh_factory/sweep_results.json + console tables.
"""
import gzip
import json
import os
import statistics
from collections import defaultdict
from itertools import product

OUT = r"C:\Users\jcole\multichain-bot\scratchpad\rh_factory"
NOTIONAL = 25.0
CAT_PCT = -50.0

def load_trips():
    out = []
    with gzip.open(os.path.join(OUT, "candidates.jsonl.gz"), "rt",
                   encoding="utf-8") as f:
        for ln in f:
            t = json.loads(ln)
            if t["day"] < "2026-07-01":
                continue
            t["chrono"] = "W1" if t["day"] <= "2026-07-05" else "W2"
            t["parity"] = "odd" if int(t["day"][8:10]) % 2 == 1 else "even"
            out.append(t)
    return out


trips = []

HALVES = [("chrono", "W1"), ("chrono", "W2"), ("parity", "odd"),
          ("parity", "even")]

AGE_BANDS = {
    "u10m": lambda a: a is not None and a < 1 / 6,
    "u1h":  lambda a: a is not None and a < 1.0,
    "1-6h": lambda a: a is not None and 1.0 <= a < 6.0,
    "6-24h": lambda a: a is not None and 6.0 <= a < 24.0,
    ">24h": lambda a: a is not None and a >= 24.0,
}
DIP_BANDS = {
    "mod":   lambda d: -25.0 <= d <= -6.0,   # winner-delta moderate pullback
    "sh":    lambda d: -12.0 <= d <= -6.0,   # shallow only
    "deep":  lambda d: d <= -12.0,           # the lane's current trigger
    "vdeep": lambda d: d <= -25.0,
}
VOL_FLOORS = {"v.3": 0.3, "v3": 3.0, "v10": 10.0}          # cum_eth
DEMANDS = {
    "d25":   lambda t: t["b30"] >= 0.015,                   # base (~$25)
    "d50n":  lambda t: t["b30"] >= 0.03 and t["b30"] > t["s30"],  # lane gate
    "d50x2": lambda t: t["b30"] >= 0.03 and t["b30"] > 2 * t["s30"],
}
EXITS = ("scalp", "aged", "tbox")


def grade(rows, half_key=None, half_val=None):
    if half_key:
        rows = [t for t in rows if t[half_key] == half_val]
    if not rows:
        return None
    return rows


def cell_stats(rows, exit_cls):
    pool_net = defaultdict(float)
    n_cat = 0
    days = set()
    rets = []
    n_stale = n_dead = 0
    for t in rows:
        r = t[exit_cls]["ret"]
        pool_net[t["pool"]] += r / 100.0 * NOTIONAL
        rets.append(r)
        if r <= CAT_PCT:
            n_cat += 1
        if t["res"] == "stale_end":
            n_stale += 1
        elif t["res"] == "dead":
            n_dead += 1
        days.add(t["day"])
    nets = sorted(pool_net.values())
    ex2 = nets[:-2] if len(nets) > 2 else nets
    return {"n": len(rows), "pools": len(pool_net),
            "tokmed_ex2": round(statistics.median(ex2), 3) if ex2 else None,
            "tokmed": round(statistics.median(nets), 3),
            "cat": round(n_cat / len(rows), 4),
            "net_usd": round(sum(nets), 2),
            "med_ret": round(statistics.median(rets), 2),
            "days": len(days),
            "stale_pct": round(100 * n_stale / len(rows), 1),
            "dead_pct": round(100 * n_dead / len(rows), 1)}


def bar_pass(st):
    return (st is not None and st["pools"] >= 20 and st["tokmed_ex2"] is not None
            and st["tokmed_ex2"] > 0 and st["cat"] <= 0.05)


def grade_cell(rows, exit_cls):
    """-> (n_pass_halves, per-half stats, overall stats)"""
    per_half = {}
    n_pass = 0
    for hk, hv in HALVES:
        sub = [t for t in rows if t[hk] == hv]
        st = cell_stats(sub, exit_cls) if sub else None
        per_half[f"{hk}:{hv}"] = st
        if bar_pass(st):
            n_pass += 1
    overall = cell_stats(rows, exit_cls) if rows else None
    return n_pass, per_half, overall


def select(age=None, dip=None, vol=None, dem=None, extra=None, rows=None):
    out = []
    afn = AGE_BANDS[age] if age else None
    dfn = DIP_BANDS[dip] if dip else None
    vfl = VOL_FLOORS[vol] if vol else None
    dmf = DEMANDS[dem] if dem else None
    for t in (rows if rows is not None else trips):
        if afn and not afn(t["age_h"]):
            continue
        if dfn and not dfn(t["dip"]):
            continue
        if vfl is not None and t["cum_eth"] < vfl:
            continue
        if dmf and not dmf(t):
            continue
        if extra and not extra(t):
            continue
        out.append(t)
    return out


REFINES = {
    "arc<=1000": lambda t: t["arc"] is not None and t["arc"] <= 1000,
    "arc<=300":  lambda t: t["arc"] is not None and t["arc"] <= 300,
    "popret":    lambda t: t["pop_ago"] is not None and t["pop_ago"] <= 1800,
    "nopop":     lambda t: t["pop_ago"] is None or t["pop_ago"] > 1800,
    "nb30>=3":   lambda t: t["nb30"] >= 3,
    "h02-10":    lambda t: 2 <= t["hour"] <= 10,
    "h13-22":    lambda t: 13 <= t["hour"] <= 22,
    "athdd>=-40": lambda t: t["athdd"] is not None and t["athdd"] >= -40,
}


def run_sweep():
    results = []
    print("[sweep] primary grid "
          f"({len(AGE_BANDS)*len(DIP_BANDS)*len(VOL_FLOORS)*len(DEMANDS)*len(EXITS)} cells)")
    for age, dip, vol, dem in product(AGE_BANDS, DIP_BANDS, VOL_FLOORS,
                                      DEMANDS):
        rows = select(age, dip, vol, dem)
        if len(rows) < 80:      # can't reach 20 pools/half anyway
            continue
        for ex in EXITS:
            n_pass, per_half, overall = grade_cell(rows, ex)
            if overall is None or overall["days"] < 5:
                continue
            results.append({"cell": f"{age}|{dip}|{vol}|{dem}|{ex}",
                            "age": age, "dip": dip, "vol": vol, "dem": dem,
                            "exit": ex, "pass_halves": n_pass,
                            "overall": overall, "halves": per_half})

    # refinement axes on the interesting cells
    base_sorted = sorted(results, key=lambda r: -r["pass_halves"])
    seeds = [r for r in base_sorted if r["pass_halves"] >= 2][:60]
    print(f"[sweep] primary cells graded={len(results)}; "
          f"refining {len(seeds)} seeds")
    for seed in seeds:
        rows0 = select(seed["age"], seed["dip"], seed["vol"], seed["dem"])
        for rname, rfn in REFINES.items():
            rows = [t for t in rows0 if rfn(t)]
            if len(rows) < 80:
                continue
            n_pass, per_half, overall = grade_cell(rows, seed["exit"])
            if overall is None or overall["days"] < 5:
                continue
            results.append({"cell": seed["cell"] + f"+{rname}",
                            "age": seed["age"], "dip": seed["dip"],
                            "vol": seed["vol"], "dem": seed["dem"],
                            "exit": seed["exit"], "refine": rname,
                            "pass_halves": n_pass, "overall": overall,
                            "halves": per_half})
    return results


def main():
    global trips
    trips.extend(load_trips())
    print(f"[sweep] {len(trips)} candidates loaded "
          f"({min(t['day'] for t in trips)}..{max(t['day'] for t in trips)})")
    results = run_sweep()
    survivors = [r for r in results if r["pass_halves"] == 4]
    survivors.sort(key=lambda r: -min(
        h["tokmed_ex2"] for h in r["halves"].values()
        if h and h["tokmed_ex2"] is not None))
    print("[sweep] 4/4 SURVIVORS:", len(survivors))
    for r in survivors[:40]:
        o = r["overall"]
        hm = min((h["tokmed_ex2"] for h in r["halves"].values()
                  if h and h["tokmed_ex2"] is not None), default=None)
        print(f"  {r['cell']:48s} n={o['n']:5d} pools={o['pools']:4d} "
              f"tokmed_ex2=${o['tokmed_ex2']:+.2f} (min-half ${hm:+.2f}) "
              f"cat={o['cat']*100:.1f}% med_ret={o['med_ret']:+.2f} "
              f"net=${o['net_usd']:+.0f} stale={o['stale_pct']}% "
              f"dead={o['dead_pct']}%")

    print("[sweep] near misses (3/4):")
    nm = sorted([r for r in results if r["pass_halves"] == 3],
                key=lambda r: -(r["overall"]["tokmed_ex2"] or -9))[:15]
    for r in nm:
        o = r["overall"]
        fails = [k for k, h in r["halves"].items() if not bar_pass(h)]
        print(f"  {r['cell']:48s} n={o['n']:5d} pools={o['pools']:4d} "
              f"tokmed_ex2=${o['tokmed_ex2']:+.2f} cat={o['cat']*100:.1f}% "
              f"FAIL={fails}")

    json.dump({"n_candidates": len(trips), "n_cells": len(results),
               "survivors": survivors, "near_misses": nm},
              open(os.path.join(OUT, "sweep_results.json"), "w"), indent=1)
    print("[sweep] wrote sweep_results.json", len(results), "cells")


if __name__ == "__main__":
    main()
