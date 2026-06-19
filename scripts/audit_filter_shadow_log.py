"""Audit filter-shadow records by computing forward realized P&L per filter.

Reads /data/filter_shadow_log.jsonl (or local copy via --path), fetches forward
30-min DexScreener candles for each (pair_address, ts) tuple, and computes the
strategy-realistic realized P&L (TP1 +5% + half-trail capped +5pp, -7% stop).

Output: per-filter table — n records, WR if let through, avg realized P&L,
total realized P&L, and a PASS-vs-BLOCK forward-EV differential. Filters where
BLOCK has lower forward EV than PASS are working; filters where BLOCK has HIGHER
forward EV are over-blocking and should be moved to SHADOW or carved out.

DEDUP-BY-PAIR: many records share a pair_address (one pair fails many filters
in a cycle). The forward candle window for each pair is fetched ONCE and reused
across every record on that pair — the big egress win.

SHADOW_BLOCK normalization: any non-PASS verdict (e.g. filter_chasing_top's
"SHADOW_BLOCK") is bucketed as BLOCK so it is no longer dropped from the diff.

Importable: compute_filter_pnl(...) returns {filter_name: {...}} and can WRITE
it to DATA_DIR/filter_shadow_pnl.json for the in-bot scorer + dashboard.

Usage:
    python scripts/audit_filter_shadow_log.py
    python scripts/audit_filter_shadow_log.py --path /local/path/log.jsonl
    python scripts/audit_filter_shadow_log.py --min-forward-min 30 --emit-json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from feeds.dexscreener_client import DexScreenerClient


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default=None,
                    help="filter_shadow_log.jsonl (default $DATA_DIR/filter_shadow_log.jsonl)")
    ap.add_argument("--min-forward-min", type=int, default=30,
                    help="Minimum minutes between record and now (skip immature records)")
    ap.add_argument("--max-records", type=int, default=2000)
    ap.add_argument("--sample-per-filter", type=int, default=300,
                    help="Cap scored records per (filter,verdict) bucket (egress bound)")
    ap.add_argument("--pace-secs", type=float, default=0.3,
                    help="Sleep between distinct-pair candle fetches (rate-limit)")
    ap.add_argument("--emit-json", action="store_true",
                    help="Write the aggregation to $DATA_DIR/filter_shadow_pnl.json")
    return ap.parse_args()


def _default_log_path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/data"),
                        "filter_shadow_log.jsonl")


def _default_out_path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/data"),
                        "filter_shadow_pnl.json")


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


# ──────────────────────────────────────────────────────────────────────────────
# PURE / TESTABLE OUTCOME MATH
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_verdict(verdict) -> str:
    """SHADOW_BLOCK (and any non-PASS) -> BLOCK; PASS -> PASS."""
    v = str(verdict or "").strip().upper()
    return "PASS" if v == "PASS" else "BLOCK"


def realized_from_bars(bars: list, block_ts: int):
    """Strategy-cap realized % from forward 1m bars relative to the bar that
    closed at/just-before block_ts. Returns (n_forward, max_gain, min_loss,
    end_pct, realized) or None. PURE — no IO."""
    if not bars:
        return None
    bars = sorted(bars, key=lambda b: b.open_time)
    block_close = None
    forward = []
    for b in bars:
        if b.open_time <= block_ts:
            block_close = b.close
        else:
            forward.append(b)
    if block_close is None or not forward or block_close == 0:
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


def _parse_block_ts(rec) -> int | None:
    try:
        return int(datetime.fromisoformat(rec["ts"]).timestamp())
    except Exception:
        return None


async def compute_filter_pnl(
    records: list,
    client,
    min_forward_min: int = 30,
    now_ts: float | None = None,
    sample_per_filter: int = 300,
    pace_secs: float = 0.3,
    out_path: str | None = None,
) -> dict:
    """Score forward realized P&L per (filter_name, verdict) with DEDUP-BY-PAIR.

    Returns {filter_name: {n, wr, avg_pct, net_pct, block_n, block_avg, pass_n,
    pass_avg, pass_block_diff}}. SHADOW_BLOCK is bucketed as BLOCK. Each distinct
    pair_address forward window is fetched EXACTLY ONCE (egress bound) and reused
    across every record sharing that pair. Records are sampled per (filter,
    verdict) bucket to bound work. FAIL-OPEN per record (a bad fetch is skipped,
    never raised). If out_path is given, the result dict is written there as JSON.

    `now_ts` is unix seconds (defaults to time.time()); maturity = age >=
    min_forward_min. `client` must expose `async fetch_1m(pair, limit=...)`.
    """
    import time as _time
    if now_ts is None:
        now_ts = _time.time()
    min_age_s = min_forward_min * 60

    # 1) Filter to MATURE records that carry a pair + block_ts.
    mature = []
    for r in records:
        bts = _parse_block_ts(r)
        if bts is None:
            continue
        if (now_ts - bts) < min_age_s:
            continue
        pair = r.get("pair_address") or ""
        if not pair:
            continue
        r = dict(r)
        r["_block_ts"] = bts
        mature.append(r)

    # 2) Per-(filter,verdict) sampling cap (bound the work / egress).
    by_bucket: dict = defaultdict(list)
    for r in mature:
        key = (r.get("filter_name"), _normalize_verdict(r.get("verdict")))
        if len(by_bucket[key]) < sample_per_filter:
            by_bucket[key].append(r)
    sampled = [r for rs in by_bucket.values() for r in rs]

    # 3) DEDUP-BY-PAIR: fetch each distinct pair's forward window exactly once.
    pairs = []
    seen = set()
    for r in sampled:
        p = r["pair_address"]
        if p not in seen:
            seen.add(p)
            pairs.append(p)
    bars_by_pair: dict = {}
    for i, p in enumerate(pairs):
        try:
            bars_by_pair[p] = await client.fetch_1m(p, limit=60)
        except Exception:
            bars_by_pair[p] = []
        if pace_secs and i + 1 < len(pairs):
            await asyncio.sleep(pace_secs)

    # 4) Score each record against its pair's (already-fetched) window.
    #    by_key[(filter, verdict)] -> list of realized %
    by_key: dict = defaultdict(list)
    for r in sampled:
        bars = bars_by_pair.get(r["pair_address"]) or []
        out = realized_from_bars(bars, r["_block_ts"])
        if out is None:
            continue
        realized = out[4]
        by_key[(r.get("filter_name"), _normalize_verdict(r.get("verdict")))].append(realized)

    # 5) Aggregate per filter (overall + PASS/BLOCK split + diff).
    filters_seen = {f for f, _ in by_key}
    result: dict = {}
    for f in sorted(filters_seen):
        block_rs = by_key.get((f, "BLOCK")) or []
        pass_rs = by_key.get((f, "PASS")) or []
        all_rs = block_rs + pass_rs
        n = len(all_rs)
        wins = sum(1 for x in all_rs if x > 0)
        block_avg = (sum(block_rs) / len(block_rs)) if block_rs else None
        pass_avg = (sum(pass_rs) / len(pass_rs)) if pass_rs else None
        diff = (pass_avg - block_avg) if (block_avg is not None and pass_avg is not None) else None
        result[f] = {
            "n": n,
            "wr": (100.0 * wins / n) if n else None,
            "avg_pct": (sum(all_rs) / n) if n else None,
            "net_pct": sum(all_rs),
            "block_n": len(block_rs),
            "block_avg": block_avg,
            "pass_n": len(pass_rs),
            "pass_avg": pass_avg,
            "pass_block_diff": diff,
        }

    if out_path:
        try:
            tmp = out_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(result, fh)
            os.replace(tmp, out_path)
        except Exception:
            pass
    return result


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

async def main():
    args = parse_args()
    path = args.path or _default_log_path()
    recs = load_records(path, args.max_records)
    print(f"Loaded {len(recs)} records from {path}")

    client = DexScreenerClient()
    out_path = _default_out_path() if args.emit_json else None
    result = await compute_filter_pnl(
        records=recs,
        client=client,
        min_forward_min=args.min_forward_min,
        sample_per_filter=args.sample_per_filter,
        pace_secs=args.pace_secs,
        out_path=out_path,
    )

    print(f"\n=== Per-filter forward outcomes (strategy-cap realized %) ===")
    print(f"{'filter':<32} {'n':<5} {'WR%':<7} {'avg':<8} {'net':<10}")
    print("-" * 70)
    for f in sorted(result):
        g = result[f]
        wr = f"{g['wr']:.1f}" if g["wr"] is not None else "--"
        avg = f"{g['avg_pct']:+.2f}%" if g["avg_pct"] is not None else "--"
        print(f"{f:<32} {g['n']:<5} {wr:<7} {avg:<8} {g['net_pct']:+9.2f}")

    print(f"\n=== Per-filter VERDICT-DIFFERENTIAL (PASS - BLOCK realized) ===")
    print("Positive = filter is correctly blocking the bad ones.")
    print("Negative = over-blocking (BLOCK cohort has better forward EV).")
    print(f"{'filter':<32} {'block_n':<8} {'pass_n':<7} {'block_avg':<10} {'pass_avg':<9} {'diff':<8}")
    print("-" * 80)
    for f in sorted(result):
        g = result[f]
        ba = f"{g['block_avg']:+.2f}" if g["block_avg"] is not None else "-"
        pa = f"{g['pass_avg']:+.2f}" if g["pass_avg"] is not None else "-"
        diff = f"{g['pass_block_diff']:+.2f}" if g["pass_block_diff"] is not None else "-"
        print(f"{f:<32} {g['block_n']:<8} {g['pass_n']:<7} {ba:<10} {pa:<9} {diff:<8}")

    if out_path:
        print(f"\nWrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
