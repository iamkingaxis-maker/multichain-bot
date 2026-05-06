"""Generic shadow-filter impact monitor.

Pulls /api/trades, filters to dip_buy buys since DEPLOY_TIME, pairs with
sells, splits by <FILTER_NAME>_verdict, emits a single status line.

Usage:
  python scripts/monitor_filter_shadow.py <filter_name> <deploy_iso_utc>
  python scripts/monitor_filter_shadow.py filter_confirmation_candle 2026-05-06T01:25:00
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone

import requests


def fetch_trades():
    r = requests.get(
        "https://gracious-inspiration-production.up.railway.app/api/trades",
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def main():
    if len(sys.argv) < 3:
        print("Usage: monitor_filter_shadow.py <filter_name> <deploy_iso_utc>")
        sys.exit(1)
    filter_name = sys.argv[1]
    deploy_time = sys.argv[2]
    verdict_key = f"{filter_name}_verdict"

    trades = fetch_trades()
    buys = [
        t for t in trades
        if t.get("type") == "buy"
        and t.get("strategy") == "dip_buy"
        and (t.get("time") or "") >= deploy_time
    ]
    sells = [t for t in trades if t.get("type") == "sell"]
    sell_idx = defaultdict(list)
    for s in sells:
        sell_idx[(s.get("address"), s.get("pair_address"))].append(s)

    # Each buy can produce MULTIPLE sells (TP1 + TP2 + trail + stop). Sells
    # belong to the most recent prior buy on the same (address, pair). Build
    # a per-key sorted buy index across ALL buys (not just since-deploy) so
    # we can bound sells by the NEXT buy on that key.
    all_buys_by_key = defaultdict(list)
    for t in trades:
        if t.get("type") == "buy" and t.get("strategy") == "dip_buy":
            all_buys_by_key[(t.get("address"), t.get("pair_address"))].append(
                t.get("time") or ""
            )
    for k in all_buys_by_key:
        all_buys_by_key[k].sort()

    passed = []
    block = []
    pass_open = 0
    block_open = 0

    for b in buys:
        em = b.get("entry_meta") or {}
        verdict = (em.get(verdict_key) or "").upper()
        bt = b.get("time") or ""
        key = (b.get("address"), b.get("pair_address"))
        # Bound: this buy's sells run until the NEXT buy on the same key
        next_bt = "9999"
        for cand_bt in all_buys_by_key.get(key, []):
            if cand_bt > bt:
                next_bt = cand_bt
                break
        # Sum ALL sells in (bt, next_bt) — TP1, TP2, trail, stop
        cands = [
            s for s in sell_idx[key]
            if bt < (s.get("time") or "") < next_bt
            and s.get("pnl") is not None
        ]
        if cands:
            pnl = sum(s.get("pnl") for s in cands)
            if verdict == "BLOCK":
                block.append(pnl)
            elif verdict == "PASS":
                passed.append(pnl)
        else:
            if verdict == "BLOCK":
                block_open += 1
            elif verdict == "PASS":
                pass_open += 1

    def stats(pnls):
        if not pnls:
            return {"n": 0, "total": 0.0, "wr": 0.0, "avg": 0.0}
        wins = sum(1 for p in pnls if p > 0)
        return {
            "n": len(pnls),
            "total": sum(pnls),
            "wr": wins / len(pnls) * 100,
            "avg": sum(pnls) / len(pnls),
        }

    p = stats(passed)
    b = stats(block)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    print(
        f"[{now}] {filter_name} since {deploy_time}: "
        f"PASS n={p['n']} (open={pass_open}) WR={p['wr']:.0f}% "
        f"total=${p['total']:+.2f} avg=${p['avg']:+.2f}  |  "
        f"BLOCK n={b['n']} (open={block_open}) WR={b['wr']:.0f}% "
        f"total=${b['total']:+.2f} avg=${b['avg']:+.2f}  |  "
        f"net delta=${b['total']:+.2f} (BLOCK total = pure delta vs old behavior)"
    )


if __name__ == "__main__":
    main()
