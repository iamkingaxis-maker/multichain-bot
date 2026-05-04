"""
Build the smart-money wallet index from closed trade history.

Walks all_trades.json (or production /api/trades), pairs buys with
their closing sells, computes per-trade PnL, and aggregates per-wallet
appearance counts on winners.

Two data sources for wallet identification:
  1. NEW (preferred): entry_meta.top_buy_makers — captured at scan time
     from recent_trades by feeds/smart_money.py:extract_top_makers.
     Available on all trades AFTER 2026-05-04 deploy of that feature.
  2. LEGACY (fallback): DexScreener internal trade log around entry
     time (1m window). Only works for tokens still queryable; fails
     silently otherwise.

Output: data/smart_money_index.json (atomic write).

Usage:
  python scripts/build_smart_money_index.py [--input all_trades.json]
                                            [--output data/smart_money_index.json]
                                            [--threshold 3]
                                            [--min-winner-pnl-pct 5.0]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

sys.stdout.reconfigure(encoding="utf-8")


def parse_ts(ts: Any):
    if not ts:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    s = str(ts)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def is_dip_close(s: Dict[str, Any]) -> bool:
    if s.get("type") != "sell":
        return False
    r = (s.get("reason") or "").lower()
    if "cancelled on restart" in r:
        return False
    return any(k in r for k in ("dip stop", "dip tp", "dip trail", "dip max", "dip stall"))


def load_paired_trades(path: str) -> List[Dict[str, Any]]:
    raw = json.load(open(path))
    trades = raw if isinstance(raw, list) else raw.get("trades", [])
    buys = [t for t in trades if t.get("type") == "buy" and t.get("strategy") == "dip_buy"]
    sells = [t for t in trades if is_dip_close(t)]
    by_pair = defaultdict(list)
    for b in buys:
        by_pair[(b.get("pair_address") or b.get("address") or "").lower()].append(b)
    for k in by_pair:
        by_pair[k].sort(key=lambda b: b.get("time", ""))
    paired = []
    for s in sells:
        key = (s.get("pair_address") or s.get("address") or "").lower()
        cands = [b for b in by_pair.get(key, []) if b.get("time", "") < s.get("time", "")]
        if not cands:
            continue
        b = cands[-1]
        paired.append({
            "pair_address": key,
            "buy_time": b.get("time"),
            "buy_ts": parse_ts(b.get("time")),
            "entry_meta": b.get("entry_meta") or {},
            "pnl": s.get("pnl") or 0.0,
            "pnl_pct": s.get("pnl_pct") or 0.0,
        })
    return paired


def extract_makers_from_meta(em: Dict[str, Any]) -> List[Tuple[str, float]]:
    """Returns [(maker_addr, volume_usd)]. Empty if not captured."""
    raw = em.get("top_buy_makers") or []
    out = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                addr = str(item.get("addr") or "")
                v = float(item.get("volume_usd") or 0)
                if addr:
                    out.append((addr, v))
    return out


def build_index(
    paired_trades: List[Dict[str, Any]],
    min_winner_pnl_pct: float,
    smart_threshold: int,
) -> Dict[str, Any]:
    """
    For each trade, look at top_buy_makers in entry_meta. Count
    appearance on winners vs losers per wallet. Surface wallets
    appearing on >=smart_threshold winners.
    """
    per_wallet = defaultdict(lambda: {
        "winners": 0,
        "losers": 0,
        "total_volume_usd": 0.0,
        "winner_volume_usd": 0.0,
    })
    n_with_makers = 0
    n_without = 0
    n_total_winners = 0
    n_total_losers = 0
    for t in paired_trades:
        is_winner = (t["pnl_pct"] or 0) >= min_winner_pnl_pct
        if is_winner:
            n_total_winners += 1
        else:
            n_total_losers += 1
        makers = extract_makers_from_meta(t["entry_meta"])
        if not makers:
            n_without += 1
            continue
        n_with_makers += 1
        for addr, vol in makers:
            stats = per_wallet[addr]
            if is_winner:
                stats["winners"] += 1
                stats["winner_volume_usd"] += vol
            else:
                stats["losers"] += 1
            stats["total_volume_usd"] += vol

    wallets = {}
    for addr, stats in per_wallet.items():
        n_appearances = stats["winners"] + stats["losers"]
        win_rate = stats["winners"] / n_appearances if n_appearances else 0.0
        avg_winner_vol = (
            stats["winner_volume_usd"] / stats["winners"]
            if stats["winners"] > 0 else 0.0
        )
        wallets[addr] = {
            "winners": stats["winners"],
            "losers": stats["losers"],
            "win_rate": round(win_rate, 3),
            "total_volume_usd": round(stats["total_volume_usd"], 2),
            "avg_winner_volume_usd": round(avg_winner_vol, 2),
        }

    n_smart = sum(1 for w in wallets.values() if w["winners"] >= smart_threshold)

    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "smart_threshold": smart_threshold,
        "min_winner_pnl_pct": min_winner_pnl_pct,
        "stats": {
            "trades_total": len(paired_trades),
            "winners_total": n_total_winners,
            "losers_total": n_total_losers,
            "trades_with_makers_captured": n_with_makers,
            "trades_without_makers_captured": n_without,
            "wallets_seen": len(wallets),
            "wallets_above_threshold": n_smart,
        },
        "wallets": wallets,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="all_trades.json")
    ap.add_argument("--output", default="data/smart_money_index.json")
    ap.add_argument("--threshold", type=int, default=3,
                    help="min winners to be considered smart")
    ap.add_argument("--min-winner-pnl-pct", type=float, default=5.0,
                    help="trade is a winner if pnl_pct >= this")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: input file not found: {args.input}")
        sys.exit(1)

    print(f"Loading paired trades from {args.input}...")
    paired = load_paired_trades(args.input)
    print(f"  {len(paired)} buy/close pairs found")

    if not paired:
        print("No trades — writing empty index")
        index = {
            "version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "smart_threshold": args.threshold,
            "min_winner_pnl_pct": args.min_winner_pnl_pct,
            "stats": {"trades_total": 0, "wallets_seen": 0, "wallets_above_threshold": 0},
            "wallets": {},
        }
    else:
        print(f"Building index (threshold={args.threshold}, "
              f"min_winner_pnl={args.min_winner_pnl_pct}%)...")
        index = build_index(paired, args.min_winner_pnl_pct, args.threshold)

    print(f"\nIndex stats:")
    for k, v in index.get("stats", {}).items():
        print(f"  {k:40s} {v}")

    if index["stats"].get("wallets_above_threshold", 0) > 0:
        print(f"\nTop 20 smart wallets (by winners):")
        sorted_w = sorted(
            index["wallets"].items(),
            key=lambda kv: (-kv[1]["winners"], -kv[1]["total_volume_usd"])
        )
        for addr, info in sorted_w[:20]:
            if info["winners"] < args.threshold:
                continue
            print(f"  {addr[:16]}... wins={info['winners']:3d} "
                  f"losers={info['losers']:3d} wr={info['win_rate']:.0%} "
                  f"avg_vol=${info['avg_winner_volume_usd']:.0f}")

    # Atomic write
    out_path = args.output
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(index, f, indent=2)
    os.replace(tmp, out_path)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
