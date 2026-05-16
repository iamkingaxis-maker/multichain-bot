"""Round 4 exhaustive mining: scan ALL populated entry_meta features,
rank by Cohen's d, then compound the ones not already in shipped triggers.

Goal: find new entry archetypes by exploring dimensions skipped in rounds 1-3.

Already-used dimensions (don't re-use):
  net_flow_60s_usd, net_flow_5m_usd, chart_mtf_score, bs_m5/h1/h6,
  chart_score, 1s_bottom_score, micro_pattern_score,
  chart_vp_poc_distance_pct, chart_reaccum_vol_return_ratio,
  1s_close_pos_60s, 1s_red_pct_60s, mean_buy_size_usd, pc_h6,
  buy_burst_30s, rt_buys_usd, pct_in_5m_range.

Search untapped: holder concentration, liquidity, vol acceleration,
chart RSI, macro_window, Tier 1/3 features, 1m features, etc.
"""
from __future__ import annotations
import requests, itertools, statistics
from collections import defaultdict

API = "https://gracious-inspiration-production.up.railway.app/api/trades?closed=true"


def cohen_d(a, b):
    if not a or not b or len(a) < 2 or len(b) < 2:
        return 0.0
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa, sb = statistics.stdev(a), statistics.stdev(b)
    n_a, n_b = len(a), len(b)
    pooled = (((n_a - 1) * sa**2 + (n_b - 1) * sb**2) / (n_a + n_b - 2)) ** 0.5
    if pooled == 0:
        return 0.0
    return (ma - mb) / pooled


def fetch():
    trades = requests.get(API, timeout=20).json()
    trades = [t for t in trades if isinstance(t, dict)]
    return [t for t in trades if t.get("pnl_pct") is not None]


SELL_TIME_FEATURES = {
    # Computed at sell time, not available at entry. EXCLUDE from triggers.
    "top10_holder_delta", "top10_holder_pct_at_sell",
    "lp_locked_pct_at_sell", "rugcheck_score_at_sell",
    "lp_imbalance_ratio_at_sell", "holder_snapshots",
    "hold_pnl_snapshots", "lp_snapshots", "rugcheck_score_snapshots",
    "orderflow_snapshots", "minutes_since_peak", "peak_pnl_pct",
    "peak_pnl_at_secs", "hold_secs",
}

ALREADY_USED = SELL_TIME_FEATURES | {
    "net_flow_60s_usd", "net_flow_5m_usd", "chart_mtf_score",
    "bs_m5", "bs_h1", "bs_h6", "chart_score", "1s_bottom_score",
    "micro_pattern_score", "chart_vp_poc_distance_pct",
    "chart_reaccum_vol_return_ratio", "1s_close_pos_60s",
    "1s_red_pct_60s", "mean_buy_size_usd", "pc_h6", "buy_burst_30s",
    "rt_buys_usd", "pct_in_5m_range", "p90_buy_size_usd",
    # Composite identifiers / non-numeric
    "trigger_source", "triggers_fired", "trigger_4combo_reasons",
    "filter_a_block_reasons", "trigger_capitv_reasons",
    "trigger_coillong_reasons", "trigger_coiltv_reasons",
    "trigger_decay4_reasons", "trigger_decay4of5_reasons",
    "trigger_engulflow_reasons", "trigger_explosive_break_reasons",
    "trigger_hc46_reasons", "trigger_high_regime_reasons",
    "trigger_hh10_strict_vol_reasons", "trigger_hh10_8plus_reasons",
    "trigger_momentum_continuation_reasons",
    "trigger_quietpop_reasons", "trigger_range_expansion_qualified_reasons",
    "trigger_squeeze_reasons", "trigger_6of7_green_vol_reasons",
    "trigger_deepbreakout_reasons", "trigger_decay5_reasons",
    "trigger_vol_velocity_2grn_reasons",
}


def main():
    paired = fetch()
    print(f"Paired closed trades: {len(paired)}")

    # Discover all numeric features across all trades
    feature_values = defaultdict(list)  # feat -> list of (value, is_win)
    for t in paired:
        m = t.get("entry_meta") or {}
        win = t["pnl_pct"] > 0
        for k, v in m.items():
            if k.endswith("_block_reasons") or k.endswith("_reasons"):
                continue
            if k.endswith("_verdict") or k.endswith("_block") or k.endswith("_pass"):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                feature_values[k].append((float(v), win))

    # Compute Cohen's d per feature (winners vs losers)
    rankings = []
    for feat, vals in feature_values.items():
        if feat in ALREADY_USED:
            continue
        wins = [v for v, w in vals if w]
        losses = [v for v, w in vals if not w]
        if len(wins) < 5 or len(losses) < 5:
            continue
        d = cohen_d(wins, losses)
        rankings.append({
            "feature": feat,
            "d": d,
            "n_wins": len(wins),
            "n_losses": len(losses),
            "win_mean": statistics.mean(wins),
            "loss_mean": statistics.mean(losses),
            "win_median": statistics.median(wins),
            "loss_median": statistics.median(losses),
        })

    rankings.sort(key=lambda x: -abs(x["d"]))
    print(f"\nTop 30 UNUSED features by |Cohen's d|:")
    print(f"{'Feature':<45} {'d':>7} {'n_w':>5} {'n_l':>5} {'win_mean':>10} {'loss_mean':>10}")
    print("-" * 95)
    for r in rankings[:30]:
        print(f"{r['feature']:<45} {r['d']:>+7.3f} {r['n_wins']:>5} {r['n_losses']:>5} "
              f"{r['win_mean']:>10.3f} {r['loss_mean']:>10.3f}")

    # For top 15, build candidate compound predicates with mean_buy>=15 wash floor
    print(f"\n\n=== Round 4 compound search ===")
    print(f"Wash floor (mean_buy>=15) + 2-3 new dims from top-15 ranked features")

    # Pull data array for trades with size_floor (mean_buy>=15) and feature coverage
    def get(t, k):
        m = t.get("entry_meta") or {}
        return m.get(k)

    base_pop = [t for t in paired if (get(t, "mean_buy_size_usd") or 0) >= 15]
    print(f"Trades with mean_buy>=15: {len(base_pop)}")
    base_wr = sum(1 for t in base_pop if t["pnl_pct"] > 0) / len(base_pop) if base_pop else 0
    print(f"Baseline WR with wash floor: {base_wr:.0%}")

    # Build predicate functions from top features
    # Heuristics: if win_mean > loss_mean, use ">= threshold"; otherwise use "< threshold"
    def threshold_from_rank(r):
        """Pick a threshold based on the win/loss medians."""
        # Threshold = midpoint between win median and loss median.
        # If win median > loss median, predicate is "feat >= threshold".
        # Else predicate is "feat <= threshold".
        wm, lm = r["win_median"], r["loss_median"]
        thr = (wm + lm) / 2
        direction = ">=" if wm > lm else "<="
        return thr, direction

    predicates = []
    for r in rankings[:15]:
        thr, dirn = threshold_from_rank(r)
        feat = r["feature"]
        if dirn == ">=":
            fn = (lambda t, k=feat, th=thr: (get(t, k) is not None) and (get(t, k) >= th))
        else:
            fn = (lambda t, k=feat, th=thr: (get(t, k) is not None) and (get(t, k) <= th))
        label = f"{feat}{dirn}{thr:.3g}"
        predicates.append((label, fn, feat))

    # Apply 2-way compounds (wash floor + 2 predicates from top-15)
    print(f"\n2-WAY compounds (wash_floor + 1 new dim), n>=8, WR>=75%:")
    results = []
    for label, fn, feat in predicates:
        cohort = [t for t in base_pop if fn(t)]
        if len(cohort) < 8:
            continue
        wins = sum(1 for t in cohort if t["pnl_pct"] > 0)
        wr = wins / len(cohort)
        if wr < 0.75:
            continue
        avg = sum(t["pnl_pct"] for t in cohort) / len(cohort)
        results.append({
            "preds": [label],
            "n": len(cohort), "wins": wins, "wr": wr, "avg": avg,
        })
    results.sort(key=lambda x: (-x["wr"], -x["n"]))
    for r in results[:15]:
        print(f"  {' & '.join(r['preds']):<50} n={r['n']:<3} WR={r['wr']:.0%} avg=+{r['avg']:.2f}%")

    # 3-WAY compounds (wash + 2 NEW dims) — only consider NEW dims, no overlap
    print(f"\n3-WAY compounds (wash_floor + 2 new dims), n>=6, WR>=90%:")
    results = []
    for (l1, fn1, f1), (l2, fn2, f2) in itertools.combinations(predicates, 2):
        if f1 == f2:
            continue
        cohort = [t for t in base_pop if fn1(t) and fn2(t)]
        if len(cohort) < 6:
            continue
        wins = sum(1 for t in cohort if t["pnl_pct"] > 0)
        wr = wins / len(cohort)
        if wr < 0.90:
            continue
        avg = sum(t["pnl_pct"] for t in cohort) / len(cohort)
        results.append({
            "preds": [l1, l2], "feats": [f1, f2],
            "n": len(cohort), "wins": wins, "wr": wr, "avg": avg,
        })
    results.sort(key=lambda x: (-x["wr"], -x["n"]))
    for r in results[:25]:
        print(f"  mean_buy>=15 & {' & '.join(r['preds']):<70} n={r['n']:<3} WR={r['wr']:.0%} avg=+{r['avg']:.2f}%")

    # 4-WAY (wash + 3 new dims)
    print(f"\n4-WAY compounds (wash_floor + 3 new dims), n>=5, WR=100%:")
    results = []
    for c in itertools.combinations(predicates, 3):
        feats = [x[2] for x in c]
        if len(set(feats)) < 3:
            continue
        cohort = [t for t in base_pop if all(fn(t) for _, fn, _ in c)]
        if len(cohort) < 5:
            continue
        wins = sum(1 for t in cohort if t["pnl_pct"] > 0)
        if wins != len(cohort):
            continue
        avg = sum(t["pnl_pct"] for t in cohort) / len(cohort)
        results.append({
            "preds": [x[0] for x in c],
            "n": len(cohort), "avg": avg,
        })
    results.sort(key=lambda x: (-x["n"], -x["avg"]))
    for r in results[:25]:
        print(f"  mean_buy>=15 & {' & '.join(r['preds']):<80} n={r['n']:<3} +{r['avg']:.2f}%")


if __name__ == "__main__":
    main()
