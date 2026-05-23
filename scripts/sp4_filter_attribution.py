"""SP4: per-filter $/tr contribution.

For each individual-ablation bot (no_<filter>):
  contribution = baseline_v1.pnl_per_trade - no_<filter>.pnl_per_trade

Positive contribution = filter HELPS (removing it makes things worse).
Negative contribution = filter HURTS (removing it makes things better).

Usage: python scripts/sp4_filter_attribution.py
Output: reports/filter_attribution.md
"""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path

from scripts.sp4_common import (
    BotMetrics, fetch_all_trades, pair_buys_sells,
    compute_metrics, confidence_label,
)


ABLATION_FILTER_MAP = {
    "no_turn": "filter_turn",
    "no_negative_net_flow_5m": "filter_negative_net_flow_5m",
    "no_seller_imbalance": "filter_seller_imbalance",
    "no_low_volatility": "filter_low_volatility",
    "no_vp_poc": "filter_vp_poc",
    "no_topping": "filter_topping",
    "no_above_vwap_chase": "filter_above_vwap_chase",
    "no_bs_m5_weak": "filter_bs_m5_weak",
    "no_blowoff_top": "filter_blowoff_top",
    "no_1m_steep_fall": "filter_1m_steep_fall",
}


def build_filter_attribution_markdown(
    baseline: BotMetrics, ablations: dict[str, BotMetrics],
) -> str:
    lines = [
        "# Filter Attribution",
        "",
        f"Baseline (`{baseline.bot_id}`): n={baseline.sample_n}, "
        f"$/tr=${baseline.pnl_per_trade or 0:+.2f}, "
        f"total=${baseline.total_pnl_usd:+.2f}",
        "",
        "**Contribution = baseline.$/tr − no_X.$/tr**",
        "Positive → filter HELPS (removing it makes things worse).",
        "Negative → filter HURTS (removing it makes things better).",
        "",
        "| Filter | Baseline n | Ablation n | Baseline $/tr | Ablation $/tr | $/tr Δ (contribution) | Confidence |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    rows = []
    base_per = baseline.pnl_per_trade or 0.0
    for bot_id, filter_name in ABLATION_FILTER_MAP.items():
        ab = ablations.get(bot_id)
        if ab is None or ab.sample_n == 0:
            rows.append((filter_name, baseline.sample_n, 0, base_per, None, None, "Very low (n<5)"))
            continue
        ab_per = ab.pnl_per_trade or 0.0
        delta = base_per - ab_per
        min_n = min(baseline.sample_n, ab.sample_n)
        rows.append((filter_name, baseline.sample_n, ab.sample_n, base_per,
                     ab_per, delta, confidence_label(min_n)))
    rows.sort(key=lambda r: r[5] if r[5] is not None else -1e9, reverse=True)
    for filter_name, base_n, ab_n, base_per, ab_per, delta, conf in rows:
        ab_per_s = f"${ab_per:+.2f}" if ab_per is not None else "—"
        delta_s = f"${delta:+.2f}" if delta is not None else "—"
        lines.append(
            f"| `{filter_name}` | {base_n} | {ab_n} | "
            f"${base_per:+.2f} | {ab_per_s} | {delta_s} | {conf} |"
        )
    return "\n".join(lines)


def main() -> int:
    trades = fetch_all_trades()
    paired = pair_buys_sells(trades)
    by_bot: dict[str, list] = defaultdict(list)
    for p in paired:
        by_bot[p.bot_id].append(p)

    baseline = compute_metrics(by_bot.get("baseline_v1", []))
    ablations = {
        bid: compute_metrics(by_bot.get(bid, []))
        for bid in ABLATION_FILTER_MAP
    }

    out_path = Path(__file__).parent.parent / "reports" / "filter_attribution.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_filter_attribution_markdown(baseline, ablations))
    print(f"Wrote filter attribution to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
