#!/usr/bin/env python
"""Offline defender-stack simulator.

Given a bot's defender filter set (and optional entry_gate) + the trade dumps,
determine which trades the stack would BLOCK and report the ENTERED (surviving)
cohort's WR / $/tr vs the unfiltered cohort — so "positive-selection signal +
full defender stack" can be validated offline instead of waiting for forward
data. (This was the gap that blocked validating champion_premium / whale /
post_peak as REAL bots rather than raw signals.)

Per (trade, filter) verdict resolution, in priority order:
  1. STAMPED   — read filter_<name>_verdict from entry_meta (production-exact,
     zero parity risk). Only ~10% of the current dumps carry stamps (they mostly
     predate the filters); coverage rises to ~100% on forward data.
  2. RECOMPUTE — for filters whose predicate + input features ARE in the dump,
     replicate dip_scanner's predicate VERBATIM (feeds/dip_scanner.py ~L5176+):
       filter_huge_wick:       wick_body_5m_avg > 10
       filter_dead_low_demand: lifecycle_stage=='dead' AND cnn_outcome_prob<0.10
                               AND bs_m5<1.0
  3. UNKNOWN   — input features absent AND no stamp -> fail-OPEN (matches
     production fail-open), and reported as uncovered so fidelity is explicit.

NOTE on fidelity: the RECOMPUTE predicates are verbatim copies of the source.
They cannot be parity-tested on the current dumps (the 2 recomputable filters
have 0 stamped examples, and the stamped filters can't be recomputed — no
input-feature overlap). Once forward data carries stamps for all 10, this tool
should prefer STAMPED everywhere and parity becomes automatic.

Usage:
  python scripts/defender_sim.py --bot champion_whale_buyers
  python scripts/defender_sim.py --filters filter_huge_wick,filter_dead_low_demand
  python scripts/defender_sim.py --bot champion_post_peak --window 2026-05-27:2026-05-29
"""
from __future__ import annotations
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ps_scan import load_completed, DEFAULT_FILES, wr, dpt  # noqa: E402

# Filters whose predicate is recomputable from persisted dump features.
# Each returns True (=BLOCK) / False (=PASS) / None (=UNKNOWN, fail-open).
def _recompute_huge_wick(f):
    v = f.get("wick_body_5m_avg")
    if v is None:
        return None
    try:
        return float(v) > 10.0
    except (TypeError, ValueError):
        return None


def _recompute_dead_low_demand(f):
    life = f.get("lifecycle_stage")
    cnn = f.get("cnn_outcome_prob")
    bsm5 = f.get("bs_m5")
    if life is None or cnn is None or bsm5 is None:
        return None
    try:
        return (life == "dead" and float(cnn) < 0.10 and float(bsm5) < 1.0)
    except (TypeError, ValueError):
        return None


RECOMPUTE = {
    "filter_huge_wick": _recompute_huge_wick,
    "filter_dead_low_demand": _recompute_dead_low_demand,
}


def verdict(filter_name, f):
    """Resolve one filter's verdict for one trade. Returns ('BLOCK'|'PASS', method)
    or (None, 'UNKNOWN')."""
    stamped = f.get(f"{filter_name}_verdict")
    if stamped is not None:
        return ("BLOCK" if str(stamped).upper() == "BLOCK" else "PASS"), "stamped"
    if filter_name in RECOMPUTE:
        r = RECOMPUTE[filter_name](f)
        if r is not None:
            return ("BLOCK" if r else "PASS"), "recompute"
    return None, "unknown"


def gate_blocks(gate, f):
    """entry_gate semantics from bot_evaluator: list of [feat, op, thr]; '<=' blocks
    when value > thr, '>=' blocks when value < thr. Fail-OPEN per condition."""
    if not gate:
        return False
    for cond in gate:
        try:
            feat, op, thr = cond[0], cond[1], float(cond[2])
        except (TypeError, ValueError, IndexError):
            continue
        v = f.get(feat)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        if op == ">=" and v < thr:
            return True
        if op == "<=" and v > thr:
            return True
    return False


def simulate(comp, filters, gate, window):
    """Return (entered, blocked, per_filter_stats)."""
    lo, hi = window if window else (None, None)

    def in_win(c):
        return True if not window else (lo <= c["t"][:10] <= hi)

    rows = [c for c in comp if in_win(c)]
    per = {fn: {"stamped": 0, "recompute": 0, "unknown": 0, "block": 0} for fn in filters}
    entered, blocked = [], []
    for c in rows:
        f = c["f"]
        is_blocked = gate_blocks(gate, f)
        for fn in filters:
            v, method = verdict(fn, f)
            per[fn][method] += 1
            if v == "BLOCK":
                per[fn]["block"] += 1
                is_blocked = True
        (blocked if is_blocked else entered).append(c)
    return rows, entered, blocked, per


def report_cohort(rows, label):
    print(f"  {label:24s} n={len(rows):4d}  WR={wr(rows):3.0f}%  $/tr={dpt(rows):+6.2f}  "
          f"total=${sum(c['pnl'] for c in rows):+8.1f}")


def main():
    ap = argparse.ArgumentParser(description="Offline defender-stack simulator")
    ap.add_argument("--bot", help="load filters_enforced + entry_gate from config/bots/<bot>.json")
    ap.add_argument("--filters", help="comma-separated filter names (overrides --bot filters)")
    ap.add_argument("--gate", help="manual entry_gate, e.g. 'top_buy_makers_n<=8' (repeatable via ;)")
    ap.add_argument("--files", default=",".join(DEFAULT_FILES))
    ap.add_argument("--window", help="LO:HI date window, e.g. 2026-05-27:2026-05-29")
    args = ap.parse_args()

    filters, gate = [], []
    if args.bot:
        path = os.path.join("config", "bots", f"{args.bot}.json")
        with open(path) as fh:
            cfg = json.load(fh)
        filters = list(cfg.get("filters_enforced") or [])
        gate = list(cfg.get("entry_gate") or [])
    if args.filters:
        filters = [x.strip() for x in args.filters.split(",") if x.strip()]
    if args.gate:
        for g in args.gate.split(";"):
            for op in ("<=", ">=", "<", ">"):
                if op in g:
                    a, b = g.split(op)
                    gate.append([a.strip(), op if op in ("<=", ">=") else (op + "="), float(b)])
                    break
    window = tuple(args.window.split(":")) if args.window else None

    files = [f.strip() for f in args.files.split(",") if f.strip()]
    print(f"Loading {files} ...", file=sys.stderr)
    comp = load_completed(files)

    print(f"\nBOT/STACK: {args.bot or '(manual)'}")
    print(f"  filters_enforced: {filters}")
    print(f"  entry_gate:       {gate or None}")
    print(f"  window:           {window or 'all'}")

    rows, entered, blocked, per = simulate(comp, filters, gate, window)

    print(f"\nPER-FILTER coverage / block-rate (fidelity transparency):")
    print(f"  {'filter':36s}{'stamped':>8}{'recomp':>8}{'unknown':>8}{'BLOCKs':>8}")
    for fn in filters:
        p = per[fn]
        print(f"  {fn:36s}{p['stamped']:8d}{p['recompute']:8d}{p['unknown']:8d}{p['block']:8d}")
    if gate:
        print(f"  entry_gate blocks counted in the combined cohort below.")

    print(f"\nCOHORTS (window {window or 'all'}):")
    report_cohort(rows, "UNFILTERED (all)")
    report_cohort(entered, "ENTERED (survives stack)")
    report_cohort(blocked, "BLOCKED (stack rejects)")
    if rows:
        lift_wr = wr(entered) - wr(rows)
        lift_d = dpt(entered) - dpt(rows)
        print(f"\n  stack lift on ENTERED cohort:  WR {lift_wr:+.1f}pp   $/tr {lift_d:+.2f}   "
              f"(blocked {len(blocked)}/{len(rows)} = {100*len(blocked)/len(rows):.0f}%)")
    cov_warn = [fn for fn in filters if per[fn]["unknown"] > 0.5 * len(rows)]
    if cov_warn:
        print(f"\n  [fidelity] >50% UNKNOWN (fail-open, not truly simulated) for: {cov_warn}")
        print(f"             -> verdict thin on this dump; rises to ~exact on forward stamped data.")


if __name__ == "__main__":
    main()
