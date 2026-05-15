"""Analyze discovered chart clusters — surface high-WR patterns.

Loads cluster_id assignments written by cluster_chart_embeddings.py.
For each cluster computes:
  - Size (N samples)
  - Win rate (% with outcome_label=1)
  - Avg/max outcome_pnl_pct
  - Dominant pattern_label distribution (from synthetic heuristics)
  - Hour-of-day distribution
  - Sample token addresses + timestamps

Output: ranked report. High-WR clusters that don't match any existing
trigger become next-trigger candidates.

Usage:
    python scripts/analyze_chart_clusters.py
"""
from __future__ import annotations
import glob
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DATASET_DIRS = [Path(".cnn_dataset/v1"), Path(".cnn_dataset/v2_broad")]


def collect():
    rows = []
    for d in DATASET_DIRS:
        json_files = glob.glob(str(d / "*.json")) + glob.glob(str(d / "**" / "*.json"), recursive=True)
        for jp in json_files:
            try:
                with open(jp) as f:
                    rows.append(json.load(f))
            except Exception:
                pass
    return rows


def main():
    rows = collect()
    all_with_cluster = [r for r in rows if r.get("cluster_id") is not None]
    # Filter to samples with strategy-cap fields populated
    rows = [r for r in all_with_cluster if "realized_pnl_strategy" in r]
    if not rows:
        print("No samples have realized_pnl_strategy. Re-run the miner with the updated script.")
        return

    print(f"Total samples with cluster_id: {len(all_with_cluster)}")
    print(f"  ...with strategy-cap fields: {len(rows)} (filtering rest)")
    print()

    # Group by cluster
    by_cluster = {}
    for r in rows:
        by_cluster.setdefault(r["cluster_id"], []).append(r)

    # Compute per-cluster stats using strategy-realistic P&L
    # realized_pnl_strategy is the truth — caps upside at TP1+trail, downside at -7% stop
    summaries = []
    for cl, items in by_cluster.items():
        n = len(items)
        # Strategy-cap WR: % of samples where realized > 0
        wins_strat = sum(1 for r in items if (r.get("outcome_label_strategy") or 0) == 1)
        wr_strat = wins_strat / n if n > 0 else 0
        # Average realized P&L
        realized = [float(r.get("realized_pnl_strategy") or 0) for r in items]
        avg_realized = sum(realized) / len(realized) if realized else 0
        max_realized = max(realized) if realized else 0
        min_realized = min(realized) if realized else 0
        # Legacy theoretical (for comparison)
        wins_theo = sum(1 for r in items if r.get("outcome_label") == 1)
        wr_theo = wins_theo / n if n > 0 else 0
        pattern_dist = Counter(r.get("pattern_label", "default") for r in items)
        # EV is now the avg realized — directly comparable to $/trade
        ev = avg_realized
        summaries.append({
            "cluster_id": cl,
            "n": n,
            "wr_strat": wr_strat,
            "wr_theo": wr_theo,
            "avg_realized": avg_realized,
            "max_realized": max_realized,
            "min_realized": min_realized,
            "ev_proxy": ev,
            "top_patterns": pattern_dist.most_common(3),
            "samples": items[:3],  # for inspection
        })

    # Sort by EV (high to low)
    summaries.sort(key=lambda s: -s["ev_proxy"])

    print(f"{'cluster':<8} {'n':<5} {'WR_str':<7} {'WR_theo':<8} {'avgPnl':<8} {'minPnl':<8} {'maxPnl':<8} dominantPatterns")
    print("-" * 110)
    for s in summaries:
        pats = ", ".join(f"{p}({n})" for p, n in s["top_patterns"])
        print(
            f"{s['cluster_id']:<8} {s['n']:<5} "
            f"{s['wr_strat']*100:<6.1f}% {s['wr_theo']*100:<7.1f}% "
            f"{s['avg_realized']:<+8.2f} {s['min_realized']:<+8.2f} {s['max_realized']:<+8.2f} {pats}"
        )

    print()
    print("TOP-3 CLUSTERS BY REALIZED EV (potential new triggers):")
    print("=" * 110)
    for s in summaries[:3]:
        print(f"\nCluster {s['cluster_id']}: n={s['n']}, WR_strat={s['wr_strat']*100:.1f}%, "
              f"avgRealized={s['avg_realized']:+.2f}% (theoretical WR was {s['wr_theo']*100:.1f}%)")
        print(f"  Dominant pattern_labels: {s['top_patterns']}")
        print(f"  Sample entries:")
        for r in s["samples"]:
            print(f"    {r.get('addr','')[:10]}... ts={r.get('ts','')[:19]} "
                  f"realized={r.get('realized_pnl_strategy',0):+.2f}% "
                  f"forward(min/max)=({r.get('forward_min_loss_pct',0):+.1f}/{r.get('forward_max_gain_pct',0):+.1f}) "
                  f"pattern={r.get('pattern_label')}")


if __name__ == "__main__":
    main()
