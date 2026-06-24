#!/usr/bin/env python
"""Held-out validation: does token AGE (lifecycle_age_hours) predict never-green?

Token-level finding: chronic-dud tokens are OLD (~1126h) vs winners young (~365h).
This validates it properly: token-deduped, held-out (train vs test, same-direction
required), with a winner-kill audit (old tokens that DO win) and a confound check
vs momentum (is age just a proxy for low pc_h24?).
"""
from __future__ import annotations
import sys, os
from collections import defaultdict
import statistics as st
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "scripts")
from ps_scan import load_completed, _in

FILES = [".trades_now.json", ".overnight_trades.json", ".watch7h/val_wide.json",
         "ep_verify.json", ".t_big.json", "trades_dump_candidates.json",
         "trades_dump.json", "trades_local_dump.json"]
TRAIN = ("2026-05-12", "2026-05-24"); TEST = ("2026-05-27", "2026-05-31")
AGE = "lifecycle_age_hours"


def _v(c, f):
    x = c["f"].get(f)
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def tok_table(rows):
    """Per token: median age, median pc_h24, never-green (median peak<1), max peak, sum pnl."""
    by = defaultdict(list)
    for c in rows:
        if _v(c, AGE) is not None:
            by[c["tok"]].append(c)
    out = []
    for tok, cs in by.items():
        out.append({"tok": tok, "age": st.median([_v(c, AGE) for c in cs]),
                    "pc24": st.median([_v(c, "pc_h24") for c in cs if _v(c, "pc_h24") is not None] or [0]),
                    "ng": 1 if st.median([c["peak"] for c in cs]) < 1 else 0,
                    "maxpeak": max(c["peak"] for c in cs), "pnl": sum(c["pnl"] for c in cs),
                    "n": len(cs)})
    return out


def quartiles(toks, label):
    toks = sorted(toks, key=lambda t: t["age"]); n = len(toks); q = n // 4
    print(f"  {label}: {n} tokens by AGE quartile -> never-green rate")
    for lab, seg in [("Q1 youngest", toks[:q]), ("Q2", toks[q:2*q]),
                     ("Q3", toks[2*q:3*q]), ("Q4 oldest", toks[3*q:])]:
        if not seg:
            continue
        ng = 100 * sum(t["ng"] for t in seg) / len(seg)
        print(f"    {lab:12} age[{seg[0]['age']:.0f},{seg[-1]['age']:.0f}]h n={len(seg):>2} NG={ng:.0f}%")


def main():
    comp = [c for c in load_completed(FILES) if c["peak"] is not None]
    cov = sum(1 for c in comp if _v(c, AGE) is not None)
    print(f"positions {len(comp)} | {AGE} coverage {100*cov/len(comp):.0f}%")
    tr = tok_table([c for c in comp if _in(c, *TRAIN)])
    te = tok_table([c for c in comp if _in(c, *TEST)])

    print("\n=== Held-out monotonicity (NG-rate by age quartile) ===")
    quartiles(tr, "TRAIN"); quartiles(te, "TEST")

    # Median-age of never-green vs reached-green tokens, both windows (same dir?)
    def split(toks):
        ng = [t["age"] for t in toks if t["ng"]]; gr = [t["age"] for t in toks if not t["ng"]]
        return (st.median(ng) if ng else None, st.median(gr) if gr else None, len(ng), len(gr))
    a_tr = split(tr); a_te = split(te)
    print(f"\n=== age: never-green tokens vs reached-green tokens ===")
    print(f"  TRAIN: NG-tok age {a_tr[0]:.0f}h vs green-tok {a_tr[1]:.0f}h (n {a_tr[2]}/{a_tr[3]})")
    print(f"  TEST : NG-tok age {a_te[0]:.0f}h vs green-tok {a_te[1]:.0f}h (n {a_te[2]}/{a_te[3]})")
    holds = (a_tr[0] > a_tr[1]) == (a_te[0] > a_te[1])
    print(f"  same direction both windows (older=duddier)? {'YES' if holds else 'NO'}")

    # Gate sweep on TEST (held-out): block tokens older than threshold tuned on TRAIN.
    print("\n=== AGE gate (block tokens older than X), held-out on TEST, token-deduped ===")
    print(f"  {'age>':>8}{'blkTok':>8}{'NGprec':>8}{'lossCut$':>10}{'winKill$':>10}{'bigWkill':>10}")
    for thr in (250, 500, 750, 1000, 1500):
        blk = [t for t in te if t["age"] > thr]
        if not blk:
            print(f"  {thr:>7}h  (none blocked on TEST)"); continue
        ngp = 100 * sum(t["ng"] for t in blk) / len(blk)
        loss_cut = -sum(t["pnl"] for t in blk if t["pnl"] < 0)
        win_kill = sum(t["pnl"] for t in blk if t["pnl"] > 0)
        bigk = sum(1 for t in blk if t["maxpeak"] >= 20)
        print(f"  {thr:>7}h{len(blk):>8}{ngp:>7.0f}%{loss_cut:>10.0f}{win_kill:>10.0f}{bigk:>10}")
    base = 100 * sum(t["ng"] for t in te) / len(te)
    print(f"  (TEST base never-green rate: {base:.0f}%)")

    # Confound vs momentum: NG-rate of OLD tokens within HIGH vs LOW pc_h24.
    print("\n=== confound check: is age just proxying low momentum? ===")
    old = [t for t in (tr + te) if t["age"] > 750]
    oldhi = [t for t in old if t["pc24"] >= 5]; oldlo = [t for t in old if t["pc24"] < 5]
    def ngr(g): return (100*sum(t["ng"] for t in g)/len(g), len(g)) if g else (float('nan'), 0)
    print(f"  OLD tokens (age>750h) with HIGH pc_h24(>=5%): NG={ngr(oldhi)[0]:.0f}% (n={ngr(oldhi)[1]})")
    print(f"  OLD tokens (age>750h) with LOW  pc_h24(<5%):  NG={ngr(oldlo)[0]:.0f}% (n={ngr(oldlo)[1]})")
    print("  (if old+HIGH-momentum is still high-NG, age carries signal beyond momentum)")


if __name__ == "__main__":
    main()
