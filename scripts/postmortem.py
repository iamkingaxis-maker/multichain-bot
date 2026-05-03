"""
Postmortem helper — given a token address, print:

  - The buy and matching sell from /api/trades
  - The full entry_meta dict at buy time
  - DexScreener chart URL for that pair
  - Loss/gain summary
  - Time-of-trade window (entry_time, exit_time, hold duration)

Output is meant to be consumed by a Claude session that then opens
the chart in Playwright, visually analyzes the price action around
entry/exit, and writes a postmortem.

Usage:
    python scripts/postmortem.py <token_address> [--api-base URL]
    python scripts/postmortem.py <token_address> --symbol BULL
    python scripts/postmortem.py latest-loss      # auto-pick worst recent loss
    python scripts/postmortem.py latest-losses 5  # top-5 recent losers

The "latest-loss" / "latest-losses N" forms scan recent dip_buy
closes and pick the worst-pnl ones.

Designed for paste-and-go: the Claude session reads this output as
the starting context for visual analysis.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_API = "https://gracious-inspiration-production.up.railway.app/api/trades?limit=2000"


def fetch_trades(api_url: str) -> List[Dict[str, Any]]:
    req = urllib.request.Request(api_url, headers={"User-Agent": "postmortem-cli"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data if isinstance(data, list) else (data.get("trades") or [])


def _is_dip_close(t: Dict[str, Any]) -> bool:
    if t.get("type") != "sell":
        return False
    reason = (t.get("reason") or "").lower()
    if "cancelled on restart" in reason:
        return False
    return any(k in reason for k in ("dip stop", "dip tp", "dip trail", "dip max", "dip stall"))


def find_pair(trades: List[Dict[str, Any]], token: str) -> Tuple[Optional[Dict], Optional[Dict]]:
    """Return (buy, sell) most-recent paired trade for given token. token can
    match either base address or symbol (case-insensitive)."""
    needle = token.strip().lower()
    buys = [
        t for t in trades
        if t.get("type") == "buy" and t.get("strategy") == "dip_buy"
        and (
            (t.get("address") or "").lower() == needle
            or (t.get("pair_address") or "").lower() == needle
            or (t.get("symbol") or "").lower() == needle
        )
    ]
    if not buys:
        return None, None
    buys.sort(key=lambda t: t.get("time", ""), reverse=True)
    buy = buys[0]
    pair_addr = (buy.get("pair_address") or buy.get("address") or "").lower()
    sells = [
        t for t in trades
        if _is_dip_close(t)
        and (
            (t.get("pair_address") or "").lower() == pair_addr
            or (t.get("address") or "").lower() == pair_addr
        )
        and t.get("time", "") > buy.get("time", "")
    ]
    sells.sort(key=lambda t: t.get("time", ""))
    sell = sells[0] if sells else None
    return buy, sell


def find_recent_losses(trades: List[Dict[str, Any]], n: int = 5) -> List[Tuple[Dict, Dict]]:
    """Return top-N worst-pnl_pct paired dip_buy trades from recent activity."""
    sells = [t for t in trades if _is_dip_close(t)]
    paired: List[Tuple[Dict, Dict]] = []
    for s in sells:
        pair_addr = (s.get("pair_address") or s.get("address") or "").lower()
        if not pair_addr:
            continue
        # Find most-recent buy on same pair before this sell
        buys = [
            t for t in trades
            if t.get("type") == "buy" and t.get("strategy") == "dip_buy"
            and (
                (t.get("pair_address") or "").lower() == pair_addr
                or (t.get("address") or "").lower() == pair_addr
            )
            and t.get("time", "") < s.get("time", "")
        ]
        if not buys:
            continue
        buys.sort(key=lambda t: t.get("time", ""), reverse=True)
        paired.append((buys[0], s))
    # Drop phantoms
    paired = [
        (b, s) for b, s in paired
        if (s.get("pnl_pct") or 0) > -15.0
    ]
    # Worst first
    paired.sort(key=lambda bs: (bs[1].get("pnl_pct") or 0))
    return paired[:n]


def render_one(buy: Dict[str, Any], sell: Optional[Dict[str, Any]]) -> str:
    out = []
    sym = buy.get("symbol") or "?"
    addr = buy.get("address", "")
    pair_addr = buy.get("pair_address") or ""
    out.append(f"=== POSTMORTEM: ${sym} ({addr[:8]}...) ===")
    out.append(f"  pair_address: {pair_addr}")
    out.append(f"  Buy time: {buy.get('time')}")
    out.append(f"  Buy size: ${buy.get('amount_usd', 0):.2f}")
    out.append(f"  Entry price: ${buy.get('price_usd', 0):.10g}")
    if sell:
        out.append(f"  Sell time: {sell.get('time')}")
        out.append(f"  Exit price: ${sell.get('price_usd', 0):.10g}")
        out.append(f"  Sell reason: {sell.get('reason', '')}")
        out.append(f"  PnL: ${sell.get('pnl_usd', 0):.2f} ({sell.get('pnl_pct', 0):+.2f}%)")
        out.append(f"  Peak PnL %: {sell.get('peak_pnl_pct', 0):+.2f}%")
        out.append(f"  Hold: {(sell.get('hold_secs') or 0)/60:.1f} min")
    else:
        out.append(f"  Sell: <still open or not closed via dip_*>")
    out.append("")
    out.append(f"  DexScreener URL: https://dexscreener.com/solana/{pair_addr}")
    out.append(f"  GeckoTerminal URL: https://www.geckoterminal.com/solana/pools/{pair_addr}")
    out.append("")
    em = buy.get("entry_meta") or {}
    if em:
        # Print the most-relevant subset — full dict can be 200+ keys
        priority = [
            "chart_full_coverage", "chart_score", "chart_verdict",
            "chart_structure_5m_verdict", "chart_sweep_5m_verdict",
            "chart_trendline_5m_verdict", "chart_reaccum_verdict",
            "chart_pattern_5m", "chart_pattern_5m_dir",
            "lifecycle_stage", "lifecycle_age_hours",
            "lifecycle_peak_h24_pct", "lifecycle_h24_ratio",
            "graduation_status", "graduation_dex_id",
            "velocity_verdict", "buys_per_min_recent", "buy_pressure_60s",
            "lp_event_verdict", "lp_delta_5m_pct",
            "wash_suspected", "unique_buyer_ratio", "top5_buyer_volume_pct",
            "median_buy_size_usd", "whale_buy_present_2k", "n_recurring_buyers_3plus",
            "top1_holder_pct", "top10_holder_pct", "dev_holder_pct",
            "lp_locked_pct", "lp_imbalance_ratio", "rugcheck_score",
            "regime", "sol_pc_h1", "btc_pc_h1", "meme_sector_pct_h24",
            "peak_h24_6h_pct", "h24_ratio_to_peak", "pct_in_1h_range",
            "1m_consec_red", "1m_volume_spike", "1m_last_close_pct",
            "filter_real_dip_3_verdict", "filter_corpse_verdict",
            "filter_fake_bounce_verdict", "filter_fofar_verdict",
            "filter_two_pattern_verdict", "filter_1m_verdict", "filter_a_verdict",
        ]
        out.append("  --- entry_meta (priority fields) ---")
        for k in priority:
            if k in em:
                out.append(f"    {k}: {em[k]!r}")
        # Hold-snapshot trajectory (if present)
        if "hold_pnl_snapshots" in em:
            out.append(f"    hold_pnl_snapshots: {em['hold_pnl_snapshots']!r}")
    out.append("")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("token", help="Token address, symbol, 'latest-loss', or 'latest-losses'")
    p.add_argument("count", nargs="?", type=int, default=5,
                   help="For 'latest-losses', how many to show (default 5)")
    p.add_argument("--api-base", default=DEFAULT_API)
    args = p.parse_args()

    trades = fetch_trades(args.api_base)
    if not trades:
        print("No trades returned from API.")
        return 1

    if args.token == "latest-loss":
        rows = find_recent_losses(trades, n=1)
    elif args.token == "latest-losses":
        rows = find_recent_losses(trades, n=args.count)
    else:
        b, s = find_pair(trades, args.token)
        if not b:
            print(f"No buy found matching {args.token!r}")
            return 1
        rows = [(b, s)]

    if not rows:
        print("No matching paired trades.")
        return 1

    for buy, sell in rows:
        print(render_one(buy, sell))


if __name__ == "__main__":
    sys.exit(main() or 0)
