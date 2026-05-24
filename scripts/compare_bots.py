#!/usr/bin/env python
"""Pairwise bot comparator + confidence checker.

Usage:
    python scripts/compare_bots.py <bot_a> <bot_b>
    python scripts/compare_bots.py <bot_a> <bot_b> --limit 5000
    python scripts/compare_bots.py <bot_a> <bot_b> --local

Pulls trades from /api/trades?full=1 (or local trades_multi.json + trades.json
with --local), filters to each bot's post-cutoff non-synthetic sells, pairs
them with their buys, and prints:

  - per-bot stats (n_trades, WR, avg/total P&L, hold time, exit reasons)
  - paired diff (bot_a minus bot_b)
  - confidence note (n needed for diff to be statistically significant at p<0.05)

Both bots are evaluated on the SAME data pull so any cross-bot comparison
is timestamp-aligned. No Railway impact — this is a local analysis tool.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
import urllib.request
from collections import Counter, defaultdict
from statistics import median, stdev


API_URL = "https://gracious-inspiration-production.up.railway.app/api/trades?full=1&limit={limit}"

# Same cutoff as scripts/sp4_common.py. Bumped 2026-05-23.
CUTOFF = "2026-05-23T15:40:00+00:00"


def fetch_trades(limit: int, local: bool) -> list[dict]:
    if local:
        # Read both legacy and multi files (post-Option-B split)
        data_dir = os.environ.get("DATA_DIR", "data")
        out: list[dict] = []
        for fname in ("trades.json", "trades_multi.json"):
            p = os.path.join(data_dir, fname)
            if os.path.exists(p):
                with open(p, "r") as f:
                    try:
                        out.extend(json.load(f))
                    except json.JSONDecodeError:
                        pass
        return out
    url = API_URL.format(limit=limit)
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.loads(resp.read())
    return data if isinstance(data, list) else data.get("trades", [])


def filter_bot(trades: list[dict], bot_id: str) -> tuple[list[dict], list[dict]]:
    """Return (buys, sells) for one bot, post-cutoff, excluding synthetic."""
    buys = []
    sells = []
    for t in trades:
        if t.get("bot_id") != bot_id:
            continue
        if (t.get("time") or "") < CUTOFF:
            continue
        reason = (t.get("reason") or "").lower()
        if "cancelled on restart" in reason:
            continue
        if t.get("type") == "buy":
            buys.append(t)
        elif t.get("type") == "sell":
            sells.append(t)
    return buys, sells


def stats_for(buys: list[dict], sells: list[dict]) -> dict:
    """Compute summary stats from paired trade records."""
    if not sells:
        return {
            "n_sells": 0, "wins": 0, "losses": 0, "win_rate_pct": 0.0,
            "total_pnl_usd": 0.0, "avg_pnl_usd": 0.0, "avg_pnl_pct": 0.0,
            "pnl_usd_stdev": 0.0, "median_hold_secs": 0,
            "exit_reasons": {}, "open_positions": len(buys),
            "pnl_series": [],
        }
    pnls_usd = [s.get("pnl", 0) or 0 for s in sells]
    pnls_pct = [s.get("pnl_pct", 0) or 0 for s in sells]
    holds = [s.get("hold_secs", 0) or 0 for s in sells if (s.get("hold_secs") or 0) > 0]
    wins = sum(1 for p in pnls_usd if p > 0)
    losses = sum(1 for p in pnls_usd if p <= 0)
    reason_counter = Counter()
    for s in sells:
        # Bucket by first phrase of reason for readability
        r = (s.get("reason") or "unknown").lower()
        if "tp1" in r: bucket = "TP1"
        elif "tp2" in r: bucket = "TP2"
        elif "stop" in r or "hard stop" in r: bucket = "stop"
        elif "trail" in r: bucket = "trail"
        elif "panic" in r: bucket = "panic"
        elif "slow_bleed" in r or "bleed" in r: bucket = "slow_bleed"
        elif "pre-stop bail" in r: bucket = "pre_stop_bail"
        elif "max hold" in r or "max_hold" in r: bucket = "max_hold"
        else: bucket = "other"
        reason_counter[bucket] += 1
    return {
        "n_sells": len(sells),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": (wins / len(sells)) * 100 if sells else 0,
        "total_pnl_usd": sum(pnls_usd),
        "avg_pnl_usd": sum(pnls_usd) / len(pnls_usd),
        "avg_pnl_pct": sum(pnls_pct) / len(pnls_pct),
        "pnl_usd_stdev": stdev(pnls_usd) if len(pnls_usd) >= 2 else 0.0,
        "median_hold_secs": median(holds) if holds else 0,
        "exit_reasons": dict(reason_counter),
        "open_positions": len(buys) - len(sells),
        "pnl_series": pnls_usd,
    }


def welch_t_pvalue(a: list[float], b: list[float]) -> float | None:
    """Welch's two-sample t-test p-value (two-tailed). Returns None if
    either group has <2 samples. Uses a normal approximation to the t
    distribution for large df (acceptable for our n>10 case)."""
    if len(a) < 2 or len(b) < 2:
        return None
    mean_a = sum(a) / len(a)
    mean_b = sum(b) / len(b)
    var_a = sum((x - mean_a) ** 2 for x in a) / (len(a) - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(var_a / len(a) + var_b / len(b))
    if se == 0:
        return 1.0
    t = (mean_a - mean_b) / se
    # Normal approximation to the t distribution (good for df>=30; rough below)
    # p = 2 * (1 - Phi(|t|))
    z = abs(t)
    # erf-based standard normal CDF
    p = 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))
    return p


def trades_needed_for_significance(
    bot_a: list[float], bot_b: list[float], target_p: float = 0.05
) -> int | None:
    """Estimate how many ADDITIONAL trades per bot would be needed for the
    observed mean difference to reach p<target_p, assuming current variance
    holds. Returns None if either group is empty or the means are equal."""
    if len(bot_a) < 2 or len(bot_b) < 2:
        return None
    mean_a = sum(bot_a) / len(bot_a)
    mean_b = sum(bot_b) / len(bot_b)
    if mean_a == mean_b:
        return None
    var_a = sum((x - mean_a) ** 2 for x in bot_a) / (len(bot_a) - 1)
    var_b = sum((x - mean_b) ** 2 for x in bot_b) / (len(bot_b) - 1)
    # Z for p=0.05 (two-tailed) is 1.96
    z_target = 1.96
    delta = abs(mean_a - mean_b)
    if delta == 0:
        return None
    # n_per_arm = (z * sqrt(var_a + var_b))^2 / delta^2
    n_required = (z_target ** 2) * (var_a + var_b) / (delta ** 2)
    n_have = min(len(bot_a), len(bot_b))
    additional = max(0, int(math.ceil(n_required)) - n_have)
    return additional


def print_row(label: str, val_a, val_b, fmt: str = "{:.2f}"):
    sa = fmt.format(val_a) if isinstance(val_a, (int, float)) else str(val_a)
    sb = fmt.format(val_b) if isinstance(val_b, (int, float)) else str(val_b)
    print(f"  {label:25s} {sa:>14s}  {sb:>14s}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("bot_a")
    p.add_argument("bot_b")
    p.add_argument("--limit", type=int, default=10000,
                   help="Trades to pull from /api/trades (default 10000)")
    p.add_argument("--local", action="store_true",
                   help="Read trades.json + trades_multi.json from local DATA_DIR instead of API")
    args = p.parse_args()

    print(f"Fetching trades...")
    trades = fetch_trades(args.limit, args.local)
    print(f"Pulled {len(trades)} records.")

    buys_a, sells_a = filter_bot(trades, args.bot_a)
    buys_b, sells_b = filter_bot(trades, args.bot_b)

    s_a = stats_for(buys_a, sells_a)
    s_b = stats_for(buys_b, sells_b)

    print()
    print(f"Bot comparison (cutoff {CUTOFF}):")
    print(f"{'':25s} {args.bot_a:>14s}  {args.bot_b:>14s}")
    print("-" * 60)
    print_row("n_buys", len(buys_a), len(buys_b), "{:d}")
    print_row("n_sells (closed)", s_a["n_sells"], s_b["n_sells"], "{:d}")
    print_row("open positions", s_a["open_positions"], s_b["open_positions"], "{:d}")
    print_row("wins", s_a["wins"], s_b["wins"], "{:d}")
    print_row("losses", s_a["losses"], s_b["losses"], "{:d}")
    print_row("win rate %", s_a["win_rate_pct"], s_b["win_rate_pct"], "{:.1f}")
    print_row("total realized $", s_a["total_pnl_usd"], s_b["total_pnl_usd"], "${:+.2f}")
    print_row("avg $/trade", s_a["avg_pnl_usd"], s_b["avg_pnl_usd"], "${:+.4f}")
    print_row("avg %/trade", s_a["avg_pnl_pct"], s_b["avg_pnl_pct"], "{:+.2f}%")
    print_row("std $/trade", s_a["pnl_usd_stdev"], s_b["pnl_usd_stdev"], "${:.4f}")
    print_row("median hold (s)", s_a["median_hold_secs"], s_b["median_hold_secs"], "{:.0f}")

    print()
    print("Exit reasons:")
    all_reasons = set(s_a["exit_reasons"]) | set(s_b["exit_reasons"])
    for r in sorted(all_reasons):
        print_row(f"  {r}", s_a["exit_reasons"].get(r, 0), s_b["exit_reasons"].get(r, 0), "{:d}")

    print()
    print("Paired diff (bot_a - bot_b):")
    print(f"  total realized $:    ${s_a['total_pnl_usd'] - s_b['total_pnl_usd']:+.2f}")
    print(f"  avg $/trade:         ${s_a['avg_pnl_usd'] - s_b['avg_pnl_usd']:+.4f}")
    print(f"  win rate diff:       {s_a['win_rate_pct'] - s_b['win_rate_pct']:+.1f} pp")

    # Confidence
    p_val = welch_t_pvalue(s_a["pnl_series"], s_b["pnl_series"])
    if p_val is not None:
        sig = "SIGNIFICANT (p<0.05)" if p_val < 0.05 else "not yet significant"
        print(f"  Welch's t-test:      p = {p_val:.3f} — {sig}")
        if p_val >= 0.05:
            need = trades_needed_for_significance(s_a["pnl_series"], s_b["pnl_series"])
            if need is not None:
                print(f"  Trades needed:       ~{need} more per bot before this diff reaches p<0.05")
            else:
                print(f"  Trades needed:       n/a (means equal or sample too small)")
    else:
        print(f"  Confidence:          insufficient data (need >=2 closed trades per bot)")


if __name__ == "__main__":
    main()
