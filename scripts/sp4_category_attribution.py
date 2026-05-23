"""SP4: per-category $/tr contribution.

Usage: python scripts/sp4_category_attribution.py
Output: reports/category_attribution.md
"""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path

from scripts.sp4_common import (
    fetch_all_trades, pair_buys_sells, compute_metrics, confidence_label,
)


CATEGORY_BOTS = [
    "no_macro_filters",
    "no_chart_pattern_filters",
    "no_structural_filters",
    "no_timing_filters",
    "no_flow_filters",
    "no_liquidity_filters",
]


def build_category_attribution_markdown(baseline, category_bots: dict) -> str:
    base_per = baseline.pnl_per_trade or 0.0
    lines = [
        "# Category Attribution",
        "",
        f"Baseline (`{baseline.bot_id}`): n={baseline.sample_n}, "
        f"$/tr=${base_per:+.2f}, total=${baseline.total_pnl_usd:+.2f}",
        "",
        "**Contribution = baseline.$/tr − no_<category>.$/tr**",
        "Positive → category helps in aggregate. Negative → category hurts.",
        "",
        "| Category | Baseline n | Ablation n | Baseline $/tr | Ablation $/tr | $/tr Δ | Confidence |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    rows = []
    for bid in CATEGORY_BOTS:
        ab = category_bots.get(bid)
        category = bid.replace("no_", "").replace("_filters", "")
        if ab is None or ab.sample_n == 0:
            rows.append((category, baseline.sample_n, 0, base_per, None, None, "Very low (n<5)"))
            continue
        ab_per = ab.pnl_per_trade or 0.0
        delta = base_per - ab_per
        min_n = min(baseline.sample_n, ab.sample_n)
        rows.append((category, baseline.sample_n, ab.sample_n, base_per,
                     ab_per, delta, confidence_label(min_n)))
    rows.sort(key=lambda r: r[5] if r[5] is not None else -1e9, reverse=True)
    for cat, base_n, ab_n, base_per_v, ab_per_v, delta, conf in rows:
        ab_per_s = f"${ab_per_v:+.2f}" if ab_per_v is not None else "—"
        delta_s = f"${delta:+.2f}" if delta is not None else "—"
        lines.append(
            f"| `{cat}` | {base_n} | {ab_n} | ${base_per_v:+.2f} | "
            f"{ab_per_s} | {delta_s} | {conf} |"
        )
    return "\n".join(lines)


def main() -> int:
    trades = fetch_all_trades()
    paired = pair_buys_sells(trades)
    by_bot: dict[str, list] = defaultdict(list)
    for p in paired:
        by_bot[p.bot_id].append(p)

    baseline = compute_metrics(by_bot.get("baseline_v1", []))
    category_metrics = {bid: compute_metrics(by_bot.get(bid, [])) for bid in CATEGORY_BOTS}

    out_path = Path(__file__).parent.parent / "reports" / "category_attribution.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_category_attribution_markdown(baseline, category_metrics))
    print(f"Wrote category attribution to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
