"""SP4: leaderboard of all bots ranked by chosen metric.

Usage: python scripts/sp4_leaderboard.py [--sort throughput_x_pnl|total_pnl_usd|pnl_per_trade|win_rate|sample_n]
Output: reports/leaderboard.md
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from pathlib import Path

from scripts.sp4_common import (
    BotMetrics, fetch_all_trades, pair_buys_sells,
    compute_metrics, confidence_label,
)


def build_leaderboard_markdown(metrics: list[BotMetrics], sort_by: str) -> str:
    sort_key_funcs = {
        "throughput_x_pnl": lambda m: m.throughput_x_pnl,
        "total_pnl_usd": lambda m: m.total_pnl_usd,
        "pnl_per_trade": lambda m: (m.pnl_per_trade if m.pnl_per_trade is not None else -1e9),
        "win_rate": lambda m: (m.win_rate if m.win_rate is not None else -1.0),
        "sample_n": lambda m: m.sample_n,
    }
    key_fn = sort_key_funcs.get(sort_by, sort_key_funcs["throughput_x_pnl"])
    sorted_metrics = sorted(metrics, key=key_fn, reverse=True)

    lines = [
        f"# Leaderboard (sorted by `{sort_by}`)",
        "",
        "| Rank | Bot | Sample | $/tr | Total P&L | WR | Best | Worst | Throughput × $/tr | Confidence |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for i, m in enumerate(sorted_metrics, start=1):
        per = f"${m.pnl_per_trade:+.2f}" if m.pnl_per_trade is not None else "—"
        wr = f"{m.win_rate * 100:.0f}%" if m.win_rate is not None else "—"
        lines.append(
            f"| {i} | `{m.bot_id}` | {m.sample_n} | {per} | "
            f"${m.total_pnl_usd:+.2f} | {wr} | "
            f"${m.best_trade_usd:+.2f} | ${m.worst_trade_usd:+.2f} | "
            f"${m.throughput_x_pnl:+.2f} | {confidence_label(m.sample_n)} |"
        )
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--sort", default="throughput_x_pnl",
        choices=["throughput_x_pnl", "total_pnl_usd", "pnl_per_trade",
                 "win_rate", "sample_n"],
    )
    args = p.parse_args()

    trades = fetch_all_trades()
    paired = pair_buys_sells(trades)
    by_bot: dict[str, list] = defaultdict(list)
    for p_ in paired:
        by_bot[p_.bot_id].append(p_)
    metrics = [compute_metrics(ps) for ps in by_bot.values()]

    out_path = Path(__file__).parent.parent / "reports" / "leaderboard.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_leaderboard_markdown(metrics, args.sort))
    print(f"Wrote leaderboard for {len(metrics)} bots to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
