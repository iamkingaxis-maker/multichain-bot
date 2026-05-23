"""SP5: validate champion is ready to promote to baseline.

5 gates: sample size, $/tr delta, drawdown, throughput, hold-out.
Exit code 0 if PASS, 1 if FAIL.

Usage: python scripts/sp5_validate_champion.py
Output: reports/sp5_validate_champion.md
"""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
from typing import Optional

from scripts.sp4_common import (
    BotMetrics, PairedTrade, fetch_all_trades, pair_buys_sells, compute_metrics,
)


MIN_SAMPLE_N = 30
MIN_PNL_DELTA = 0.10
DRAWDOWN_TOLERANCE = 1.2
THROUGHPUT_FLOOR_RATIO = 0.5


def check_sample_size(champion: BotMetrics, baseline: BotMetrics) -> tuple[bool, str]:
    if champion.sample_n < MIN_SAMPLE_N:
        return False, f"champion.sample_n={champion.sample_n} < {MIN_SAMPLE_N}"
    if baseline.sample_n < MIN_SAMPLE_N:
        return False, f"baseline.sample_n={baseline.sample_n} < {MIN_SAMPLE_N}"
    return True, f"champion n={champion.sample_n}, baseline n={baseline.sample_n} (both >= {MIN_SAMPLE_N})"


def check_pnl_delta(champion: BotMetrics, baseline: BotMetrics) -> tuple[bool, str]:
    c_per = champion.pnl_per_trade or 0.0
    b_per = baseline.pnl_per_trade or 0.0
    delta = c_per - b_per
    if delta < MIN_PNL_DELTA:
        return False, (
            f"champion ${c_per:+.3f}/tr vs baseline ${b_per:+.3f}/tr "
            f"= delta ${delta:+.3f} < required ${MIN_PNL_DELTA:.2f}"
        )
    return True, f"delta ${delta:+.3f}/tr >= required ${MIN_PNL_DELTA:.2f}"


def check_drawdown(champion: BotMetrics, baseline: BotMetrics) -> tuple[bool, str]:
    allowed_worst = baseline.worst_trade_usd * DRAWDOWN_TOLERANCE
    if champion.worst_trade_usd < allowed_worst:
        return False, (
            f"champion worst ${champion.worst_trade_usd:+.2f} is worse than "
            f"baseline worst ${baseline.worst_trade_usd:+.2f} * {DRAWDOWN_TOLERANCE} "
            f"= ${allowed_worst:+.2f}"
        )
    return True, (
        f"champion worst ${champion.worst_trade_usd:+.2f} >= "
        f"baseline worst * {DRAWDOWN_TOLERANCE} = ${allowed_worst:+.2f}"
    )


def check_throughput(champion: BotMetrics, baseline: BotMetrics) -> tuple[bool, str]:
    floor = baseline.sample_n * THROUGHPUT_FLOOR_RATIO
    if champion.sample_n < floor:
        return False, (
            f"champion sample {champion.sample_n} < baseline {baseline.sample_n} * "
            f"{THROUGHPUT_FLOOR_RATIO} = {floor:.0f} (champion not firing enough)"
        )
    return True, f"champion {champion.sample_n} >= floor {floor:.0f}"


def check_holdout(champion_pairs: list[PairedTrade]) -> tuple[bool, str]:
    if len(champion_pairs) < 10:
        return True, f"too few pairs ({len(champion_pairs)}) - holdout gate skipped"
    sorted_pairs = sorted(champion_pairs, key=lambda p: p.time)
    split_idx = int(len(sorted_pairs) * 0.7)
    holdout = sorted_pairs[split_idx:]
    if not holdout:
        return True, "holdout empty - skipped"
    holdout_per = sum(p.realized_pnl_usd for p in holdout) / len(holdout)
    if holdout_per <= 0:
        return False, (
            f"holdout $/tr ${holdout_per:+.3f} <= 0 "
            f"({len(holdout)} late trades vs {split_idx} early)"
        )
    return True, (
        f"holdout $/tr ${holdout_per:+.3f} > 0 "
        f"({len(holdout)} late trades vs {split_idx} early)"
    )


def validate_all(champion_metrics, baseline_metrics, champion_pairs) -> tuple[bool, str]:
    gates = [
        ("Sample size", check_sample_size(champion_metrics, baseline_metrics)),
        ("$/tr delta", check_pnl_delta(champion_metrics, baseline_metrics)),
        ("Drawdown", check_drawdown(champion_metrics, baseline_metrics)),
        ("Throughput", check_throughput(champion_metrics, baseline_metrics)),
        ("Hold-out", check_holdout(champion_pairs)),
    ]
    all_pass = all(ok for _, (ok, _) in gates)
    verdict = "PASS" if all_pass else "FAIL"
    lines = [f"# Champion validation - {verdict}", ""]
    for name, (ok, msg) in gates:
        icon = "PASS" if ok else "FAIL"
        lines.append(f"## {name}: {icon}")
        lines.append(f"- {msg}")
        lines.append("")
    return all_pass, "\n".join(lines)


def main() -> int:
    trades = fetch_all_trades()
    paired = pair_buys_sells(trades)
    by_bot: dict[str, list] = defaultdict(list)
    for p in paired:
        by_bot[p.bot_id].append(p)
    champion_pairs = by_bot.get("champion_proposal", [])
    baseline_pairs = by_bot.get("baseline_v1", [])
    champion_metrics = compute_metrics(champion_pairs)
    baseline_metrics = compute_metrics(baseline_pairs)
    ok, report = validate_all(champion_metrics, baseline_metrics, champion_pairs)
    out_path = Path(__file__).parent.parent / "reports" / "sp5_validate_champion.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(report)
    print(f"\nWrote report to {out_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
