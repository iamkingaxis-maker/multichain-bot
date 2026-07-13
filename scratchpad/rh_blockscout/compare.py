# scratchpad/rh_blockscout/compare.py
"""SHADOW GRADER — Blockscout free-API features vs the eth_getLogs
reconstruction, joined per {"ev":"rug_signals"} ledger row.

Both sources now land on the SAME row (compute_entry_stamp merges bs_* alongside
the reconstruction). This reads the RH paper ledger, keeps rows carrying BOTH,
and answers: does Blockscout AGREE with the on-chain replay, and is it
faster/more-complete? That decision graduates Blockscout from shadow (bs_
replaces the 40-60-call reconstruction) once agreement holds.

Field pairs graded (bs_  <->  reconstruction; identical definitions):
  bs_hidden_supply_share_pct  <->  visible_float_pct   (100 - pool - top10)
  bs_top10_pct                <->  top10_pct
  bs_top1_pct                 <->  top1_pct
  bs_shoulder_11_20_pct       <->  shoulder_11_20_pct
  bs_pool_pct                 <->  pool_pct_of_supply

Caveats surfaced, not hidden:
  * n_holders (replay, real holders ex-pool/dead) and bs_holders_count (explorer
    total) use different denominators -> reported side-by-side, NOT diffed.
  * bs_* is scored over ONE holders page (<=50); a token with mass far down the
    tail can under-read top10 slightly. bs_n_holders_ranked flags the page depth.
  * the reconstruction row may be `truncated` (budget blown) -> its holder
    numbers are partial; those rows are the ones Blockscout most helps.

Usage:
  python scratchpad/rh_blockscout/compare.py [--ledger PATH] [--tol 3.0]
"""
import argparse
import json
import os
import statistics
import sys

DEFAULT_LEDGER = os.path.join("scratchpad", "robinhood_tapes",
                              "rh_paper_trades.jsonl")

PAIRS = [
    ("bs_hidden_supply_share_pct", "visible_float_pct"),
    ("bs_top10_pct", "top10_pct"),
    ("bs_top1_pct", "top1_pct"),
    ("bs_shoulder_11_20_pct", "shoulder_11_20_pct"),
    ("bs_pool_pct", "pool_pct_of_supply"),
]


def _load(path):
    rows = []
    if not os.path.exists(path):
        print(f"[compare] no ledger at {path}", file=sys.stderr)
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("ev") == "rug_signals":
                rows.append(r)
    return rows


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def grade(rows, tol):
    both = [r for r in rows
            if r.get("bs_source_ok") and r.get("top10_pct") is not None]
    print(f"rug_signals rows: {len(rows)}   "
          f"with BOTH sources: {len(both)}   tol=+-{tol}pp\n")
    if not both:
        print("no dual-source rows yet — accrue stamps from a live lane session.")
        return

    print(f"{'field pair':<42}{'n':>4}{'mean|Δ|':>9}{'median|Δ|':>10}"
          f"{'within tol':>11}{'bias(bs-recon)':>15}")
    print("-" * 91)
    for bs_k, rc_k in PAIRS:
        diffs = []
        signed = []
        for r in both:
            a, b = _num(r.get(bs_k)), _num(r.get(rc_k))
            if a is None or b is None:
                continue
            diffs.append(abs(a - b))
            signed.append(a - b)
        if not diffs:
            print(f"{bs_k + ' vs ' + rc_k:<42}{0:>4}{'--':>9}")
            continue
        within = sum(1 for d in diffs if d <= tol) / len(diffs) * 100.0
        print(f"{bs_k + ' vs ' + rc_k:<42}{len(diffs):>4}"
              f"{statistics.mean(diffs):>9.2f}"
              f"{statistics.median(diffs):>10.2f}"
              f"{within:>10.0f}%"
              f"{statistics.mean(signed):>+15.2f}")

    # completeness & cost: the OTHER axis of the graduation decision.
    print("\n-- completeness / cost --")
    recon_trunc = sum(1 for r in both if r.get("truncated"))
    recon_err = sum(1 for r in both if r.get("err"))
    print(f"reconstruction truncated: {recon_trunc}/{len(both)}   "
          f"errored: {recon_err}/{len(both)}")
    costs = [r.get("cost", {}).get("rpc_calls") for r in both]
    costs = [c for c in costs if isinstance(c, (int, float))]
    secs = [r.get("cost", {}).get("secs") for r in both]
    secs = [s for s in secs if isinstance(s, (int, float))]
    if costs:
        print(f"reconstruction RPC calls/stamp: "
              f"median {statistics.median(costs):.0f}  max {max(costs):.0f}")
    if secs:
        print(f"reconstruction wall secs/stamp:  "
              f"median {statistics.median(secs):.1f}  max {max(secs):.1f}")
    print("Blockscout: 2 HTTP calls/token (meta+holders), 10-min cached, "
          "~1-6s per NEW token.")

    # holders-count sanity (different denominators — side by side, not diffed).
    hc = [(r.get("bs_holders_count"), r.get("n_holders"))
          for r in both
          if _num(r.get("bs_holders_count")) and _num(r.get("n_holders"))]
    if hc:
        print("\n-- holders count (NOT comparable: explorer-total vs replay-"
              "real-ex-pool) --")
        for a, b in hc[:12]:
            print(f"  bs_holders_count={a:<8} recon n_holders(real)={b}")

    # disagreement spotlight — the rows to eyeball before graduating.
    print("\n-- largest hidden-supply disagreements --")
    ranked = []
    for r in both:
        a, b = _num(r.get("bs_hidden_supply_share_pct")), _num(r.get("visible_float_pct"))
        if a is not None and b is not None:
            ranked.append((abs(a - b), r.get("sym"), a, b, r.get("truncated")))
    for d, sym, a, b, trunc in sorted(ranked, reverse=True)[:8]:
        flag = " [recon truncated]" if trunc else ""
        print(f"  {str(sym):<14} bs={a:6.2f}  recon={b:6.2f}  Δ={d:5.2f}{flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--tol", type=float, default=3.0,
                    help="agreement tolerance in percentage points")
    a = ap.parse_args()
    grade(_load(a.ledger), a.tol)


if __name__ == "__main__":
    main()
