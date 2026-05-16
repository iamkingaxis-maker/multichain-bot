"""Mine entry_meta features that distinguish post-exit CONTINUERS
from DUMPERS on the trail-exit cohort.

Hypothesis (from trail_postexit_check): ~50% of trail-exit tokens
continue running after our exit (we left money on table), ~50% dump
or die (exiting was correct). If we can identify continuers at ENTRY
time, we can apply a different exit strategy to each cohort.

Method:
  1. Pull all trail-exit trades (last 14d).
  2. Classify each post-exit:
     - CONTINUER: post-exit max > +2% above our exit AND drift_30m > 0
     - DUMPER:    no post-exit candles (token died) OR drift_30m < -2%
     - MIXED:     everything else (excluded)
  3. Pull entry_meta features (snapshotted at buy time) for each trade.
  4. Cohen's d on every numeric feature between CONTINUERS and DUMPERS.
  5. Report top discriminators (|d| >= 0.6, n>=5 each cohort).
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from feeds.dexscreener_client import DexScreenerClient

DASHBOARD_URL = "https://gracious-inspiration-production.up.railway.app"
WINDOW_DAYS = 14
POST_EXIT_WINDOW_S = 30 * 60


def parse_iso(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00") if "Z" in s else s
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def fetch_trades():
    url = f"{DASHBOARD_URL}/api/trades?limit=2000"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


async def classify_post_exit(client, pair, exit_ts):
    try:
        candles = await client.fetch_1m(pair, limit=200)
    except Exception:
        return "DUMPER", None
    pre = [c for c in candles if c.open_time <= exit_ts]
    post = [c for c in candles
            if exit_ts < c.open_time <= exit_ts + POST_EXIT_WINDOW_S]
    if not pre or not post:
        return "DUMPER", None  # no post-exit trades = token died
    anchor = pre[-1].close
    if anchor <= 0:
        return "DUMPER", None
    max_above = (max(c.high for c in post) / anchor - 1) * 100
    min_below = (min(c.low for c in post) / anchor - 1) * 100
    drift = (post[-1].close / anchor - 1) * 100
    if max_above >= 2.0 and drift > 0:
        return "CONTINUER", {"max_above": max_above, "min_below": min_below, "drift": drift}
    if drift < -2.0:
        return "DUMPER", {"max_above": max_above, "min_below": min_below, "drift": drift}
    return "MIXED", {"max_above": max_above, "min_below": min_below, "drift": drift}


def pair_buy_sell(trades):
    by_key = {}
    for t in trades:
        if t.get("strategy") not in ("dip_buy", "scanner"):
            continue
        key = (t.get("token"), round(t.get("entry_price", 0), 10))
        by_key.setdefault(key, []).append(t)
    out = []
    for key, events in by_key.items():
        buys = [e for e in events if e.get("type") == "buy"]
        sells = [e for e in events if e.get("type") == "sell"]
        if not buys or not sells:
            continue
        buy = buys[0]
        sells.sort(key=lambda x: x.get("time", ""))
        last = sells[-1]
        out.append({
            "token": key[0],
            "entry_time": buy.get("time"),
            "exit_time": last.get("time"),
            "pair_address": last.get("pair_address") or buy.get("pair_address"),
            "peak_pnl_pct": last.get("peak_pnl_pct") or 0,
            "actual_pnl_pct": last.get("pnl_pct") or 0,
            "reason": last.get("reason", ""),
            "entry_meta": buy.get("entry_meta") or {},
        })
    return out


def cohen_d(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3 or len(b) < 3:
        return None
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    if len(a) < 2 or len(b) < 2:
        return None
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    pooled = math.sqrt((va + vb) / 2)
    if pooled == 0:
        return None
    return (ma - mb) / pooled


async def main():
    trades = fetch_trades()
    pairs = pair_buy_sell(trades)
    print(f"Total paired positions: {len(pairs)}")

    cutoff = datetime.now(timezone.utc).timestamp() - WINDOW_DAYS * 24 * 3600
    trail_cohort = []
    for p in pairs:
        dt = parse_iso(p["entry_time"])
        if not dt or dt.timestamp() < cutoff:
            continue
        if not p["reason"].startswith("Dip trail"):
            continue
        if not p["pair_address"]:
            continue
        trail_cohort.append(p)
    print(f"Trail-exit trades in last {WINDOW_DAYS}d: {len(trail_cohort)}")
    if not trail_cohort:
        return

    # Classify each post-exit
    client = DexScreenerClient()
    cont, dump, mixed = [], [], []
    print("\nClassifying post-exit behavior ...")
    for i, p in enumerate(trail_cohort):
        exit_dt = parse_iso(p["exit_time"])
        if not exit_dt:
            continue
        verdict, _ = await classify_post_exit(client, p["pair_address"], exit_dt.timestamp())
        if verdict == "CONTINUER":
            cont.append(p)
        elif verdict == "DUMPER":
            dump.append(p)
        else:
            mixed.append(p)
        await asyncio.sleep(0.05)
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(trail_cohort)} done — cont={len(cont)} dump={len(dump)} mixed={len(mixed)}")

    print(f"\nCohorts: CONTINUERS={len(cont)}  DUMPERS={len(dump)}  MIXED={len(mixed)} (excluded)")
    if len(cont) < 3 or len(dump) < 3:
        print("Cohorts too small for stable Cohen's d.")
        return

    # Collect all numeric features
    all_feats = set()
    for p in cont + dump:
        em = p["entry_meta"]
        if isinstance(em, dict):
            for k, v in em.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    all_feats.add(k)
    # Exclusion: shadow/verdict/derived bookkeeping fields
    EXCL_PREFIXES = ('trigger_', 'filter_', 'AE_', 'AD_', 'AF_', 'WW_', 'cnn_', '_')
    EXCL_SUFFIXES = ('_match', '_verdict', '_reasons', '_block', '_pass', '_at_sell',
                     '_delta', '_shadow', '_rescue')
    clean_feats = [k for k in all_feats
                   if not any(k.startswith(p) for p in EXCL_PREFIXES)
                   and not any(k.endswith(s) for s in EXCL_SUFFIXES)]

    print(f"\nFeatures evaluated: {len(clean_feats)}")
    results = []
    for k in clean_feats:
        a = [p["entry_meta"][k] for p in cont
             if isinstance(p["entry_meta"].get(k), (int, float))
             and not isinstance(p["entry_meta"].get(k), bool)]
        b = [p["entry_meta"][k] for p in dump
             if isinstance(p["entry_meta"].get(k), (int, float))
             and not isinstance(p["entry_meta"].get(k), bool)]
        d = cohen_d(a, b)
        if d is None or abs(d) < 0.4:
            continue
        if len(a) < 5 or len(b) < 5:
            continue
        results.append({
            "feature": k,
            "d": d,
            "cont_mean": sum(a) / len(a),
            "cont_n": len(a),
            "dump_mean": sum(b) / len(b),
            "dump_n": len(b),
        })
    results.sort(key=lambda r: -abs(r["d"]))

    print(f"\n=== Top features distinguishing CONTINUERS (n={len(cont)}) from DUMPERS (n={len(dump)}) ===")
    print(f"  d > 0 → higher value = more likely CONTINUER (good)")
    print(f"  d < 0 → higher value = more likely DUMPER (bad)")
    print(f"  {'Feature':<42} {'d':>6} {'cont_mean':>11} {'dump_mean':>11}  {'cont_n':>6} {'dump_n':>6}")
    for r in results[:30]:
        print(f"  {r['feature']:<42} {r['d']:>+5.2f}  "
              f"{r['cont_mean']:>+10.2f}  {r['dump_mean']:>+10.2f}  "
              f"{r['cont_n']:>5}  {r['dump_n']:>5}")

    # Save sample tokens per cohort
    print(f"\nCONTINUERS sample (highest peak): {[p['token'] for p in sorted(cont, key=lambda x: -x['peak_pnl_pct'])[:8]]}")
    print(f"DUMPERS sample (highest peak):    {[p['token'] for p in sorted(dump, key=lambda x: -x['peak_pnl_pct'])[:8]]}")


if __name__ == "__main__":
    asyncio.run(main())
