# scripts/rh_liq_seed_export.py
"""Export the RH cold-start liq seed (config/rh_liq_seed.json).

The Railway rh-paper-lane container ships with NO scratchpad/ (.railwayignore
excludes it), so the aged-mode feed boots with zero known liquidity and the
audition starves (see the COLD-START block in scripts/rh_chain_feed.py).
This exporter distills the LOCAL lane's pools_meta.jsonl (discovered +
snapshot rows) into a tiny {pool: last_known_liq_usd} map that DOES ship in
the deploy. The feed uses it for audition ORDER only — a stale entry can
never promote a pool without a fresh passing balanceOf check — so running
this before a deploy is helpful but never required for correctness.

Filters: last row per pool, liq >= MIN_LIQ (RH_FEED_MIN_LIQ, default 5000),
projected age (age_h at sighting + elapsed since) <= --max-age-h (default 96:
the 72h feed window + deploy slack; the feed age-prunes anyway). Cap: top
--cap pools by liq (default 400) to keep the file tiny.

Usage: python scripts/rh_liq_seed_export.py
       [--meta scratchpad/robinhood_tapes/pools_meta.jsonl]
       [--out config/rh_liq_seed.json] [--max-age-h 96] [--cap 400]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MIN_LIQ = float(os.environ.get("RH_FEED_MIN_LIQ", "5000"))


def parse_ts(iso: str) -> float:
    """ISO8601 (+00:00) -> epoch; 0.0 on garbage."""
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).timestamp()
    except Exception:
        return 0.0


def build_seed(rows: list, now: float, min_liq: float = MIN_LIQ,
               max_age_h: float = 96.0, cap: int = 400) -> dict:
    """pools_meta rows -> {pool: liq}. Last row per pool wins; below-floor,
    over-age and unparseable rows drop. Pure (unit-tested)."""
    last = {}
    for r in rows:
        pool = str(r.get("pool") or "").lower()
        # strict address shape: 0x + 40 hex chars (keeps synthetic test rows
        # like "0xseeded" out of the shipped artifact)
        if len(pool) != 42 or not pool.startswith("0x"):
            continue
        try:
            int(pool[2:], 16)
        except ValueError:
            continue
        ts = parse_ts(str(r.get("ts") or ""))
        if ts <= 0:
            continue
        prev = last.get(pool)
        if prev is None or ts >= prev[0]:
            last[pool] = (ts, r.get("liq"), r.get("age_h"))
    out = {}
    for pool, (ts, liq, age_h) in last.items():
        try:
            liq = float(liq)
            age_now = float(age_h) + (now - ts) / 3600.0
        except (TypeError, ValueError):
            continue
        if liq >= min_liq and 0.0 <= age_now <= max_age_h:
            out[pool] = round(liq, 2)
    top = sorted(out.items(), key=lambda kv: -kv[1])[:cap]
    return dict(top)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", default=os.path.join(
        "scratchpad", "robinhood_tapes", "pools_meta.jsonl"))
    ap.add_argument("--out", default=os.path.join("config", "rh_liq_seed.json"))
    ap.add_argument("--max-age-h", type=float, default=96.0)
    ap.add_argument("--cap", type=int, default=400)
    args = ap.parse_args()

    rows = []
    with open(args.meta, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    now = time.time()
    pools = build_seed(rows, now, MIN_LIQ, args.max_age_h, args.cap)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"generated_utc": time.strftime(
                       "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now)),
                   "source": args.meta, "min_liq": MIN_LIQ,
                   "pools": pools}, f, indent=0, sort_keys=True)
        f.write("\n")
    print(f"[seed] {len(pools)} pools (liq>=${MIN_LIQ:.0f}, "
          f"age<={args.max_age_h:.0f}h) -> {args.out}")


if __name__ == "__main__":
    main()
