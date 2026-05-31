#!/usr/bin/env python
"""Rework the entry-quality gate as a HIGH-PRECISION CONJUNCTION rule-miner.

The linear loss-score (entry_gate_validate.py) failed: averaging weak separators
across incompatible archetypes (dip winners vs momentum winners) trades winners
~1:1 at every threshold. This miner takes the opposite approach — find the small
CORNER of feature space that is almost purely losers (the TinyWorld 'dust + fake
bounce + dead tape' archetype), where genuine winners rarely fail every dimension
at once. A conjunction of independent weak conditions can reach high precision in
a narrow region even when no single feature separates.

  condition  = one-sided cut on a universal separator (loss-favorable direction).
  rule       = AND of 1..K conditions. BLOCK a trade when it meets ALL of them.
  precision  = blocked LOSS tokens / blocked tokens   (how pure is the corner)
  recall     = blocked LOSS tokens / all LOSS tokens  (how much trash it catches)
  winner-kill= blocked WIN tokens / all WIN tokens    (collateral damage)

All metrics TOKEN-DEDUPED (a token is 'blocked' if >50% of its fires are blocked)
so a correlated cluster (TinyWorld x30) can't fake precision. Rule is mined on
TRAIN, then FROZEN and validated on held-out TEST. Gate 'works' iff TEST precision
is high AND big-winner-kill <= cap with meaningful recall.

Usage:
    python scripts/entry_gate_rework.py
    python scripts/entry_gate_rework.py --trigger channel_pos_swing   # per-trigger
    python scripts/entry_gate_rework.py --min-prec 0.80 --max-k 3 --kill-cap 0.05
"""
from __future__ import annotations
import argparse
import statistics as st
from itertools import combinations
from collections import defaultdict

from ps_scan import load_completed, _in
from win_loss_diff import DEFAULT_FILES, DEFAULT_TRAIN, DEFAULT_TEST

# Universal separators (loss-favorable direction = the side losers sit on).
# LOWER => losers have LOW values (condition: value <= cut).
# HIGHER => losers have HIGH values (condition: value >= cut).
FEATURES = [
    ("buy_size_mean_prior60s", "LOWER"),       # dust buyers
    ("buy_size_max_prior60s", "LOWER"),
    ("buy_size_stddev_last60s", "LOWER"),
    ("net_flow_5m_n", "LOWER"),
    ("filter_weak_bounce_body_over_range", "HIGHER"),  # flat fake bounce
    ("lower_wick_ratio_5m", "LOWER"),                  # no rejection wick
    ("1m_max_drop", "HIGHER"),                          # shallow/no flush (drop near 0)
    ("1m_range_pct_last", "LOWER"),                     # dead tape
    ("chart_entry_range_pct", "LOWER"),
    ("shape_30m_max_over_entry_pct", "LOWER"),
]


def _val(c, feat):
    v = c["f"].get(feat)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def candidate_cuts(rows, feat, direction):
    """Loss-favorable one-sided cuts at deciles of the TRAIN value distribution."""
    vals = sorted(v for c in rows if (v := _val(c, feat)) is not None)
    if len(vals) < 40:
        return []
    qs = [vals[int(len(vals) * p)] for p in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)]
    return sorted(set(qs))


def cond_hits(c, feat, direction, cut):
    """Does trade c satisfy this loss-favorable condition? (None feature => no hit)."""
    v = _val(c, feat)
    if v is None:
        return False
    return v <= cut if direction == "LOWER" else v >= cut


def token_eval(rows, conds, big_peak):
    """Token-deduped precision/recall/kill for a rule = AND of `conds`.

    conds: list of (feat, direction, cut). A trade is blocked if it hits ALL conds
    that are APPLICABLE (feature present). A trade with a missing feature in the
    conjunction cannot be blocked (fail-open => no false block on missing data).
    """
    by = defaultdict(list)
    for c in rows:
        # applicable = every cond's feature present; else cannot block this trade
        appl = all(_val(c, f) is not None for f, _, _ in conds)
        blocked = appl and all(cond_hits(c, f, d, q) for f, d, q in conds)
        by[c["tok"]].append((c, blocked))
    win = loss = bwin = 0
    bl_win = bl_loss = bl_bwin = bl_tot = 0
    for tok, lst in by.items():
        cs = [c for c, _ in lst]
        is_win = st.median([c["pnl"] for c in cs]) > 0
        pk = [c["peak"] for c in cs if c["peak"] is not None]
        is_bwin = is_win and pk and max(pk) >= big_peak
        blocked = sum(1 for _, b in lst if b) > len(lst) / 2.0
        win += is_win; loss += (not is_win); bwin += bool(is_bwin)
        if blocked:
            bl_tot += 1; bl_win += is_win; bl_loss += (not is_win); bl_bwin += bool(is_bwin)
    prec = (bl_loss / bl_tot) if bl_tot else 0.0
    recall = (bl_loss / loss) if loss else 0.0
    kill = (bl_win / win) if win else 0.0
    bkill = (bl_bwin / bwin) if bwin else 0.0
    return {"prec": prec, "recall": recall, "kill": kill, "bkill": bkill,
            "bl_tot": bl_tot, "bl_loss": bl_loss, "bl_win": bl_win,
            "loss": loss, "win": win, "bwin": bwin}


def mine(train, test, features, min_prec, max_k, kill_cap, big_peak, min_recall_loss=3):
    # Build candidate conditions from TRAIN.
    conds = []
    for feat, direction in features:
        for cut in candidate_cuts(train, feat, direction):
            conds.append((feat, direction, cut))
    # Search conjunctions up to size max_k; keep best by TRAIN recall s.t. precision/kill ok.
    best = None
    for k in range(1, max_k + 1):
        for combo in combinations(conds, k):
            feats = [f for f, _, _ in combo]
            if len(set(feats)) != k:        # no two cuts on the same feature
                continue
            m = token_eval(train, list(combo), big_peak)
            if (m["prec"] >= min_prec and m["bkill"] <= kill_cap
                    and m["kill"] <= kill_cap * 2 and m["bl_loss"] >= min_recall_loss):
                key = (m["recall"], m["prec"])
                if best is None or key > best[0]:
                    best = (key, combo, m)
    return best


def show(label, m):
    print(f"  {label}: block {m['bl_tot']} tok ({m['bl_loss']}L/{m['bl_win']}W) "
          f"prec={m['prec']*100:.0f}% recall={m['recall']*100:.0f}% "
          f"kill={m['kill']*100:.1f}% bigkill={m['bkill']*100:.1f}% "
          f"[L={m['loss']} W={m['win']} bigW={m['bwin']}]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", default=",".join(DEFAULT_FILES))
    ap.add_argument("--train", default=f"{DEFAULT_TRAIN[0]}:{DEFAULT_TRAIN[1]}")
    ap.add_argument("--test", default=f"{DEFAULT_TEST[0]}:{DEFAULT_TEST[1]}")
    ap.add_argument("--trigger", default=None, help="mine within one trigger only")
    ap.add_argument("--min-prec", type=float, default=0.80)
    ap.add_argument("--max-k", type=int, default=3)
    ap.add_argument("--kill-cap", type=float, default=0.05)
    ap.add_argument("--big-peak", type=float, default=20.0)
    args = ap.parse_args()
    train_w = tuple(args.train.split(":")); test_w = tuple(args.test.split(":"))
    files = [f.strip() for f in args.files.split(",") if f.strip()]

    comp = load_completed(files)
    if args.trigger:
        comp = [c for c in comp if args.trigger in c["trig"]]
    tr = [c for c in comp if _in(c, *train_w)]
    te = [c for c in comp if _in(c, *test_w)]
    scope = f"trigger={args.trigger}" if args.trigger else "ALL triggers (universal)"
    print(f"scope: {scope} | TRAIN {len(tr)} trades | TEST {len(te)} trades")
    print(f"target: precision>={args.min_prec*100:.0f}%, big-winner-kill<={args.kill_cap*100:.0f}%, K<={args.max_k}")
    print()

    best = mine(tr, te, FEATURES, args.min_prec, args.max_k, args.kill_cap, args.big_peak)
    if not best:
        print("NO RULE on TRAIN meets the precision/kill bar. (corner not pure enough)")
        return
    _, combo, mtr = best
    print("MINED RULE (BLOCK when ALL hold):")
    for f, d, q in combo:
        print(f"   {f} {'<=' if d=='LOWER' else '>='} {q:.4g}")
    print()
    show("TRAIN", mtr)
    mte = token_eval(te, list(combo), args.big_peak)
    show("TEST ", mte)
    print()
    verdict = ("WORKS" if (mte["prec"] >= 0.70 and mte["bkill"] <= args.kill_cap
                           and mte["bl_loss"] >= 2) else "FAILS held-out")
    print(f"HELD-OUT VERDICT: {verdict}  (test precision {mte['prec']*100:.0f}%, "
          f"big-kill {mte['bkill']*100:.0f}%, losers caught {mte['bl_loss']})")


if __name__ == "__main__":
    main()
