"""Audit filter-shadow records by computing forward realized P&L per filter.

Reads /data/filter_shadow_log.jsonl (or local copy via --path), fetches forward
30-min DexScreener candles for each (pair_address, ts) tuple, and computes the
strategy-realistic realized P&L (TP1 +5% + half-trail capped +5pp, -7% stop).

Output: per-filter table — n records, WR if let through, avg realized P&L,
total realized P&L. Filters where BLOCK has lower forward EV than PASS are
working; filters where BLOCK has HIGHER forward EV are over-blocking and
should be moved to SHADOW or carved out.

Usage:
    python scripts/audit_filter_shadow_log.py
    python scripts/audit_filter_shadow_log.py --path /local/path/log.jsonl
    python scripts/audit_filter_shadow_log.py --min-forward-min 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from feeds.dexscreener_client import DexScreenerClient


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default="/data/filter_shadow_log.jsonl")
    ap.add_argument("--min-forward-min", type=int, default=30,
                    help="Minimum minutes between record and now (skip immature records)")
    ap.add_argument("--max-records", type=int, default=2000)
    return ap.parse_args()


def load_records(path: str, max_n: int) -> list[dict]:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: {path} not found")
        sys.exit(1)
    recs = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                recs.append(json.loads(line))
            except Exception:
                continue
    if len(recs) > max_n:
        recs = recs[-max_n:]
    return recs


async def fetch_forward_outcome(client: DexScreenerClient, pair: str,
                                block_ts: int) -> tuple:
    """Return (n_forward_bars, max_gain%, min_loss%, end%, realized%) or None."""
    try:
        bars = await client.fetch_1m(pair, limit=60)
    except Exception:
        return None
    if not bars:
        return None
    bars.sort(key=lambda b: b.open_time)
    block_close = None
    forward = []
    for b in bars:
        if b.open_time <= block_ts:
            block_close = b.close
        else:
            forward.append(b)
    if block_close is None or not forward:
        return None
    max_high = max(b.high for b in forward)
    min_low = min(b.low for b in forward)
    end_close = forward[-1].close
    max_gain = (max_high / block_close - 1) * 100
    min_loss = (min_low / block_close - 1) * 100
    end_pct = (end_close / block_close - 1) * 100
    if min_loss <= -7:
        realized = -7.0
    elif max_gain >= 5:
        realized = 2.5 + min((max_gain - 5.0) * 0.5, 5.0)
    else:
        realized = max(end_pct, -7.0)
    return (len(forward), max_gain, min_loss, end_pct, realized)


async def main():
    args = parse_args()
    recs = load_records(args.path, args.max_records)
    print(f"Loaded {len(recs)} records from {args.path}")

    now = datetime.now(timezone.utc)
    min_age_s = args.min_forward_min * 60
    mature = []
    for r in recs:
        try:
            rec_ts = datetime.fromisoformat(r["ts"])
            age = (now - rec_ts).total_seconds()
            if age >= min_age_s:
                r["_block_ts"] = int(rec_ts.timestamp())
                mature.append(r)
        except Exception:
            continue
    print(f"  mature (>= {args.min_forward_min}min old): {len(mature)}")

    client = DexScreenerClient()
    results = []
    for i, r in enumerate(mature):
        pair = r.get("pair_address") or ""
        if not pair:
            continue
        out = await fetch_forward_outcome(client, pair, r["_block_ts"])
        if out is None:
            continue
        n_fwd, mx, mn, end, real = out
        results.append({
            "filter_name": r.get("filter_name"),
            "verdict": r.get("verdict"),
            "max_gain_pct": mx,
            "min_loss_pct": mn,
            "end_pct": end,
            "realized": real,
        })
        if (i + 1) % 25 == 0:
            print(f"  processed {i+1}/{len(mature)}")
        await asyncio.sleep(0.3)

    # Aggregate per filter × verdict
    print(f"\n=== Per-filter forward outcomes (strategy-cap realized %) ===")
    print(f"{'filter':<30} {'verdict':<8} {'n':<5} {'WR%':<7} {'avg_rl':<8} {'net_rl':<10}")
    print("-" * 75)
    by_key = defaultdict(list)
    for r in results:
        by_key[(r["filter_name"], r["verdict"])].append(r["realized"])
    rows = []
    for (f, v), rs in by_key.items():
        n = len(rs)
        wins = sum(1 for x in rs if x > 0)
        avg = sum(rs) / n
        net = sum(rs)
        rows.append((f, v, n, wins / n * 100, avg, net))
    rows.sort(key=lambda x: x[0])
    for f, v, n, wr, avg, net in rows:
        print(f"{f:<30} {v:<8} {n:<5} {wr:<6.1f} {avg:+7.2f}% {net:+9.2f}")

    print(f"\n=== Per-filter VERDICT-DIFFERENTIAL (PASS - BLOCK realized) ===")
    print("Positive = filter is correctly blocking the bad ones.")
    print("Negative = filter is over-blocking (BLOCK cohort has better forward EV).")
    filters_seen = set(f for f, _ in by_key)
    print(f"{'filter':<30} {'block_n':<8} {'pass_n':<7} {'block_avg':<10} {'pass_avg':<9} {'diff':<8}")
    print("-" * 80)
    for f in sorted(filters_seen):
        block_rs = by_key.get((f, "BLOCK")) or []
        pass_rs = by_key.get((f, "PASS")) or []
        block_avg = sum(block_rs) / max(len(block_rs), 1) if block_rs else None
        pass_avg = sum(pass_rs) / max(len(pass_rs), 1) if pass_rs else None
        if block_avg is None or pass_avg is None:
            diff_str = "-"
        else:
            diff_str = f"{pass_avg - block_avg:+.2f}"
        ba = f"{block_avg:+.2f}" if block_avg is not None else "-"
        pa = f"{pass_avg:+.2f}" if pass_avg is not None else "-"
        print(f"{f:<30} {len(block_rs):<8} {len(pass_rs):<7} {ba:<10} {pa:<9} {diff_str:<8}")


if __name__ == "__main__":
    asyncio.run(main())
