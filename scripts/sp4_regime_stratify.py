"""SP4: per-bot $/tr stratified by macro regime.

Usage: python scripts/sp4_regime_stratify.py
Output: reports/regime_stratify.md
"""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from scripts.sp4_common import (
    fetch_all_trades, pair_buys_sells, confidence_label, PairedTrade,
)


def sol_h1_bucket(v) -> str:
    if v is None:
        return "unknown"
    if v < -0.3:
        return "red"
    if v > 0.3:
        return "green"
    return "flat"


def pc_h24_bucket(v) -> str:
    if v is None:
        return "unknown"
    if v < -20:
        return "deep_red"
    if v < -5:
        return "red"
    if v < 5:
        return "flat"
    if v < 30:
        return "green"
    return "pumped"


def utc_hour(time_iso: str) -> int:
    try:
        dt = datetime.fromisoformat(time_iso.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).hour
    except Exception:
        return -1


def _summarize_bucket(pairs):
    n = len(pairs)
    if n == 0:
        return 0, None
    return n, sum(p.realized_pnl_usd for p in pairs) / n


def build_regime_stratify_markdown(by_bot):
    lines = ["# Regime Stratification", ""]
    lines.append("Per-bot $/tr bucketed by macro regime at entry time.")
    lines.append("")

    lines.append("## SOL h1 regime")
    lines.append("")
    lines.append("| Bot | red n | red $/tr | flat n | flat $/tr | green n | green $/tr |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for bot_id in sorted(by_bot.keys()):
        bucket_pairs = defaultdict(list)
        for p in by_bot[bot_id]:
            sol_h1 = (p.buy_meta or {}).get("sol_pc_h1")
            bucket_pairs[sol_h1_bucket(sol_h1)].append(p)
        red_n, red_per = _summarize_bucket(bucket_pairs.get("red", []))
        flat_n, flat_per = _summarize_bucket(bucket_pairs.get("flat", []))
        green_n, green_per = _summarize_bucket(bucket_pairs.get("green", []))
        red_s = f"${red_per:+.2f}" if red_per is not None else "—"
        flat_s = f"${flat_per:+.2f}" if flat_per is not None else "—"
        green_s = f"${green_per:+.2f}" if green_per is not None else "—"
        lines.append(
            f"| `{bot_id}` | {red_n} | {red_s} | {flat_n} | {flat_s} | {green_n} | {green_s} |"
        )

    lines.append("")
    lines.append("## pc_h24 regime")
    lines.append("")
    lines.append("| Bot | deep_red n | deep_red $/tr | red n | red $/tr | flat n | flat $/tr | green n | green $/tr | pumped n | pumped $/tr |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for bot_id in sorted(by_bot.keys()):
        bucket_pairs = defaultdict(list)
        for p in by_bot[bot_id]:
            pch = (p.buy_meta or {}).get("pc_h24")
            bucket_pairs[pc_h24_bucket(pch)].append(p)
        cells = []
        for b in ["deep_red", "red", "flat", "green", "pumped"]:
            n, per = _summarize_bucket(bucket_pairs.get(b, []))
            per_s = f"${per:+.2f}" if per is not None else "—"
            cells.append(f"{n}")
            cells.append(per_s)
        lines.append(f"| `{bot_id}` | " + " | ".join(cells) + " |")

    lines.append("")
    lines.append("## Time of day (UTC hour) — bots with n>=10")
    lines.append("")
    for bot_id in sorted(by_bot.keys()):
        pairs = by_bot[bot_id]
        if len(pairs) < 10:
            continue
        by_hour = defaultdict(list)
        for p in pairs:
            h = utc_hour(p.time)
            if 0 <= h <= 23:
                by_hour[h].append(p)
        if not by_hour:
            continue
        lines.append(f"### `{bot_id}`")
        lines.append("")
        lines.append("| Hour UTC | n | $/tr |")
        lines.append("|---:|---:|---:|")
        for h in range(24):
            pairs_h = by_hour.get(h, [])
            n, per = _summarize_bucket(pairs_h)
            per_s = f"${per:+.2f}" if per is not None else "—"
            lines.append(f"| {h:02d} | {n} | {per_s} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    trades = fetch_all_trades()
    paired = pair_buys_sells(trades)
    by_bot: dict[str, list] = defaultdict(list)
    for p in paired:
        by_bot[p.bot_id].append(p)

    out_path = Path(__file__).parent.parent / "reports" / "regime_stratify.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_regime_stratify_markdown(by_bot))
    print(f"Wrote regime stratification to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
