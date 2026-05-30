#!/usr/bin/env python
"""Design + held-out validation of the UNIVERSAL entry-quality gate.

The win-vs-loss differential (win_loss_diff.py) surfaced 3 mechanisms that
separate winning fires from losing fires across the WHOLE trigger catalog,
validated against a permutation null (11 observed recurring separators vs 0.4
under the null). This gate composes them into a single pre-trigger floor:

  (1) BUYER QUALITY   buy_size_mean_prior60s    HIGHER = win   (real buyers, not dust)
  (2) FAKE-BOUNCE     filter_weak_bounce_body_over_range  LOWER = win  (rejection, not flat fake bounce)
  (3) LIVENESS        1m_range_pct_last         HIGHER = win   (live candle, not dead tape)
  (4) KNIFE           1m_max_drop               LOWER = win    (drop contained, not still falling)
  (5) REJECTION WICK  lower_wick_ratio_5m       HIGHER = win   (low got defended)

Gate = BLOCK when >= K of the AVAILABLE mechanisms FAIL their threshold.
Fail-open per feature: a missing feature cannot fail (so sparse coverage never
blocks). This is the same fail-open-per-condition design as the positive-selection
entry_gates already in production.

DISCIPLINE (feedback_held_out_validation + winner_kill_audit):
  * Thresholds + K are tuned on TRAIN ONLY (grid search), then FROZEN.
  * Reported on TEST, which the params never saw.
  * Winner-kill is audited token-deduped, on ALL winners and on BIG winners
    (peak_pnl_pct >= 20). Hard gate: <= 5% of big winners killed.
  * Loser-block, winner-kill, and $/tr lift all reported token-deduped
    ($/tr is otherwise contaminated by cap2k $650 sizing).

Usage:
    python scripts/entry_gate_validate.py
    python scripts/entry_gate_validate.py --kill-cap 0.05 --big-peak 20
"""
from __future__ import annotations
import argparse
import statistics as st
from collections import defaultdict

from ps_scan import load_completed, _in
from win_loss_diff import DEFAULT_FILES, DEFAULT_TRAIN, DEFAULT_TEST

# (feature, direction) — direction is the WINNING side. These are the universal
# (>=4-trigger, held-out, null-validated) separators EXCLUDING pc_h24 extension,
# which is context-dependent (helps momentum triggers, hurts dips) — not universal.
MECHANISMS = [
    ("buy_size_mean_prior60s", "HIGHER"),     # buyer quality
    ("buy_size_max_prior60s", "HIGHER"),
    ("buy_size_stddev_last60s", "HIGHER"),
    ("net_flow_5m_n", "HIGHER"),
    ("filter_weak_bounce_body_over_range", "LOWER"),  # fake bounce
    ("lower_wick_ratio_5m", "HIGHER"),                # rejection wick
    ("1m_max_drop", "LOWER"),
    ("1m_range_pct_last", "HIGHER"),          # liveness
    ("chart_entry_range_pct", "HIGHER"),
    ("shape_30m_max_over_entry_pct", "HIGHER"),
]


def _val(c, feat):
    v = c["f"].get(feat)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def coverage(rows, feat):
    return sum(1 for c in rows if _val(c, feat) is not None)


def robust_stats(train_rows):
    """Per-feature TRAIN median + MAD scale (robust z normalization)."""
    stats = {}
    for feat, direction in MECHANISMS:
        vals = [_val(c, feat) for c in train_rows if _val(c, feat) is not None]
        if len(vals) < 30:
            continue
        med = st.median(vals)
        mad = st.median([abs(v - med) for v in vals]) or st.pstdev(vals) or 1e-9
        stats[feat] = (direction, med, mad * 1.4826)
    return stats


def loss_score(c, stats):
    """Mean signed z in the LOSS-LIKE direction over AVAILABLE features (fail-open).

    Higher score = more loss-like. HIGHER=win feature contributes -(z) (low value =
    loss-like); LOWER=win contributes +z (high value = loss-like).
    """
    zs = []
    for feat, (direction, med, scale) in stats.items():
        v = _val(c, feat)
        if v is None:
            continue
        z = (v - med) / scale
        zs.append(-z if direction == "HIGHER" else z)
    return (sum(zs) / len(zs)) if zs else None


def blocked(c, stats, T):
    s = loss_score(c, stats)
    return s is not None and s >= T


def tok_dedup(rows):
    """Collapse to one row per token; win if median pnl>0; peak = max peak."""
    by = defaultdict(list)
    for c in rows:
        by[c["tok"]].append(c)
    out = []
    for tok, cs in by.items():
        pk = [c["peak"] for c in cs if c["peak"] is not None]
        out.append({"tok": tok, "pnl": st.median([c["pnl"] for c in cs]),
                    "win": st.median([c["pnl"] for c in cs]) > 0,
                    "peak": max(pk) if pk else None})
    return out


def metrics(rows, stats, T, big_peak):
    """Token-deduped block/kill metrics for a (stats,T) score-gate over `rows`."""
    # Tag each trade blocked/kept, then dedup per token by majority-blocked.
    by = defaultdict(list)
    for c in rows:
        by[c["tok"]].append((c, blocked(c, stats, T)))
    toks = {}
    for tok, lst in by.items():
        cs = [c for c, _ in lst]
        pk = [c["peak"] for c in cs if c["peak"] is not None]
        toks[tok] = {
            "win": st.median([c["pnl"] for c in cs]) > 0,
            "peak": max(pk) if pk else None,
            "blocked": sum(1 for _, b in lst if b) > len(lst) / 2.0,
            "pnl": st.median([c["pnl"] for c in cs]),
        }
    wins = [t for t in toks.values() if t["win"]]
    losses = [t for t in toks.values() if not t["win"]]
    bigw = [t for t in toks.values() if t["peak"] is not None and t["peak"] >= big_peak and t["win"]]
    def rate(sub):
        return (sum(1 for t in sub if t["blocked"]) / len(sub)) if sub else 0.0
    kept = [t for t in toks.values() if not t["blocked"]]
    return {
        "n_tok": len(toks),
        "loser_block": rate(losses), "n_loss": len(losses),
        "winner_kill": rate(wins), "n_win": len(wins),
        "bigwin_kill": rate(bigw), "n_bigwin": len(bigw),
        "dpt_all": st.mean([t["pnl"] for t in toks.values()]) if toks else 0.0,
        "dpt_kept": st.mean([t["pnl"] for t in kept]) if kept else 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", default=",".join(DEFAULT_FILES))
    ap.add_argument("--train", default=f"{DEFAULT_TRAIN[0]}:{DEFAULT_TRAIN[1]}")
    ap.add_argument("--test", default=f"{DEFAULT_TEST[0]}:{DEFAULT_TEST[1]}")
    ap.add_argument("--kill-cap", type=float, default=0.05, help="max big-winner kill rate")
    ap.add_argument("--big-peak", type=float, default=20.0)
    args = ap.parse_args()
    train_w = tuple(args.train.split(":"))
    test_w = tuple(args.test.split(":"))
    files = [f.strip() for f in args.files.split(",") if f.strip()]

    comp = load_completed(files)
    tr = [c for c in comp if _in(c, *train_w)]
    te = [c for c in comp if _in(c, *test_w)]
    print(f"positions: {len(comp)} | TRAIN {len(tr)} | TEST {len(te)}")
    print("coverage (TRAIN):", {f: f"{100*coverage(tr,f)/len(tr):.0f}%" for f, _ in MECHANISMS})
    stats = robust_stats(tr)
    print(f"features with usable TRAIN stats: {len(stats)}/{len(MECHANISMS)}")
    print()

    # ---- GRID SEARCH on TRAIN: pick loss-score threshold T maximizing loser-block
    #      subject to big-winner kill <= cap. T swept over the loss-score percentiles.
    best = None
    print("GRID SEARCH (TRAIN only) — loss-score T -> loser_block / winner_kill / bigwin_kill")
    for T in (0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.75, 0.90):
        m = metrics(tr, stats, T, args.big_peak)
        ok = m["bigwin_kill"] <= args.kill_cap
        print(f"  T={T:.2f}: block={m['loser_block']*100:5.1f}%  "
              f"kill={m['winner_kill']*100:5.1f}%  bigkill={m['bigwin_kill']*100:5.1f}%  "
              f"{'OK' if ok else 'x'}")
        # maximize loser-block minus winner-kill (favor a real separation, not blanket blocking)
        sep = m["loser_block"] - m["winner_kill"]
        if ok and (best is None or sep > best[0]):
            best = (sep, T)
    if not best:
        print("\nNo threshold stays under the big-winner kill cap on TRAIN.")
        return
    _, T = best
    mtr = metrics(tr, stats, T, args.big_peak)
    print(f"\nFROZEN gate: block if loss_score >= {T:.2f}")
    print(f"  (TRAIN: block {mtr['loser_block']*100:.0f}% of losers, kill {mtr['winner_kill']*100:.0f}% of winners, "
          f"sep +{(mtr['loser_block']-mtr['winner_kill'])*100:.0f}pp)")
    for feat, (d, med, scale) in stats.items():
        print(f"   {feat:36} {d:6} med={med:.4g} scale={scale:.4g}")

    # ---- APPLY FROZEN gate to held-out TEST.
    print("\n" + "=" * 72)
    print("HELD-OUT TEST RESULT (params never saw this window)")
    print("=" * 72)
    mt = metrics(te, stats, T, args.big_peak)
    print(f"  tokens (test):        {mt['n_tok']}")
    print(f"  LOSER block rate:     {mt['loser_block']*100:5.1f}%  ({mt['n_loss']} loss tokens)")
    print(f"  winner kill rate:     {mt['winner_kill']*100:5.1f}%  ({mt['n_win']} win tokens)")
    print(f"  BIG-winner kill rate: {mt['bigwin_kill']*100:5.1f}%  ({mt['n_bigwin']} big-win tokens, peak>={args.big_peak:.0f})  "
          f"[cap {args.kill_cap*100:.0f}% -> {'PASS' if mt['bigwin_kill']<=args.kill_cap else 'FAIL'}]")
    print(f"  $/tr all tokens:      {mt['dpt_all']:+.3f}")
    print(f"  $/tr after gate:      {mt['dpt_kept']:+.3f}   (lift {mt['dpt_kept']-mt['dpt_all']:+.3f})")
    print("  NOTE: $/tr size-contaminated by cap2k $650 positions; block/kill rates are token-deduped and size-independent.")


if __name__ == "__main__":
    main()
