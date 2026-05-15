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
    rows = [r for r in rows if r.get("cluster_id") is not None]
    if not rows:
        print("No cluster_id found. Run cluster_chart_embeddings.py first.")
        return

    print(f"Total samples with cluster_id: {len(rows)}")
    print()

    # Group by cluster
    by_cluster = {}
    for r in rows:
        by_cluster.setdefault(r["cluster_id"], []).append(r)

    # Compute per-cluster stats
    summaries = []
    for cl, items in by_cluster.items():
        n = len(items)
        wins = sum(1 for r in items if r.get("outcome_label") == 1)
        wr = wins / n if n > 0 else 0
        pnls = [float(r.get("outcome_pnl_pct") or 0) for r in items]
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0
        max_pnl = max(pnls) if pnls else 0
        pattern_dist = Counter(r.get("pattern_label", "default") for r in items)
        # EV-ranking: WR * avg_gain (a rough proxy)
        ev = wr * avg_pnl
        summaries.append({
            "cluster_id": cl,
            "n": n,
            "wins": wins,
            "wr": wr,
            "avg_pnl_pct": avg_pnl,
            "max_pnl_pct": max_pnl,
            "ev_proxy": ev,
            "top_patterns": pattern_dist.most_common(3),
            "samples": items[:3],  # for inspection
        })

    # Sort by EV (high to low)
    summaries.sort(key=lambda s: -s["ev_proxy"])

    print(f"{'cluster':<8} {'n':<5} {'WR':<7} {'avgPnl':<8} {'maxPnl':<8} {'EV':<7} dominantPatterns")
    print("-" * 100)
    for s in summaries:
        pats = ", ".join(f"{p}({n})" for p, n in s["top_patterns"])
        print(f"{s['cluster_id']:<8} {s['n']:<5} {s['wr']*100:<6.1f}% {s['avg_pnl_pct']:<+8.1f} {s['max_pnl_pct']:<+8.1f} {s['ev_proxy']:<+7.2f} {pats}")

    print()
    print("TOP-3 CLUSTERS BY EV (potential new triggers):")
    print("=" * 100)
    for s in summaries[:3]:
        print(f"\nCluster {s['cluster_id']}: n={s['n']}, WR={s['wr']*100:.1f}%, avgPnl={s['avg_pnl_pct']:+.1f}%")
        print(f"  Dominant pattern_labels: {s['top_patterns']}")
        print(f"  Sample entries:")
        for r in s["samples"]:
            print(f"    {r.get('addr','')[:10]}... ts={r.get('ts','')[:19]} "
                  f"win={r.get('outcome_label')} pnl={r.get('outcome_pnl_pct',0):+.1f}% "
                  f"pattern={r.get('pattern_label')}")


if __name__ == "__main__":
    main()
