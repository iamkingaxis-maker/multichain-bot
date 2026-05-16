"""mtf_strong_downtrend false-block audit.

For each signal-event where block_filter='mtf_strong_downtrend',
fetch the token's forward 60-min price behavior. Compute:
  - would-have-won rate (peak >= +5 in 60 min)
  - avg peak after block
  - avg drift_60m

If false-block rate is HIGH (>30%), we're filtering out winners and
should loosen the threshold or add another carve-out.

If LOW (<15%), the filter is doing its job.
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from feeds.dexscreener_client import DexScreenerClient

DASHBOARD_URL = "https://gracious-inspiration-production.up.railway.app"
WINDOW_S = 60 * 60

_UNIVERSE_PAIRS = None
_TRADE_PAIRS = None


def parse_iso(s):
    s = s.replace("Z", "+00:00") if "Z" in s else s
    return datetime.fromisoformat(s)


def _load_universe_pairs():
    global _UNIVERSE_PAIRS
    if _UNIVERSE_PAIRS is not None: return _UNIVERSE_PAIRS
    try:
        data = json.loads(Path("universe_fresh.json").read_text())
    except Exception:
        _UNIVERSE_PAIRS = {}; return _UNIVERSE_PAIRS
    out = {}
    for e in data:
        s = e.get("symbol"); p = e.get("pair_address")
        if s and p: out.setdefault(s, p)
    _UNIVERSE_PAIRS = out
    return out


def _load_trade_pairs():
    global _TRADE_PAIRS
    if _TRADE_PAIRS is not None: return _TRADE_PAIRS
    try:
        with urllib.request.urlopen(f"{DASHBOARD_URL}/api/trades?limit=2000") as r:
            trades = json.loads(r.read())
    except Exception:
        _TRADE_PAIRS = {}; return _TRADE_PAIRS
    out = {}
    for t in trades:
        s = t.get("token"); p = t.get("pair_address")
        if s and p: out.setdefault(s, p)
    _TRADE_PAIRS = out
    return out


def lookup_pair(sym):
    return _load_universe_pairs().get(sym) or _load_trade_pairs().get(sym)


async def fetch_forward(client, pair, anchor_ts):
    try:
        candles = await client.fetch_1m(pair, limit=200)
    except Exception:
        return None
    pre = [c for c in candles if c.open_time <= anchor_ts]
    post = [c for c in candles if anchor_ts < c.open_time <= anchor_ts + WINDOW_S]
    if not pre or not post: return None
    anchor = pre[-1].close
    if anchor <= 0: return None
    return {
        "peak_above": (max(c.high for c in post) / anchor - 1) * 100,
        "min_below": (min(c.low for c in post) / anchor - 1) * 100,
        "drift_60m": (post[-1].close / anchor - 1) * 100,
    }


async def main():
    with urllib.request.urlopen(f"{DASHBOARD_URL}/api/signal-events?limit=2000") as r:
        events = json.loads(r.read())
    events = events if isinstance(events, list) else events.get("events", events.get("rows", []))

    # Filter to mtf_strong_downtrend blocks
    mtf_blocks = [e for e in events if e.get("block_filter") == "mtf_strong_downtrend"]
    print(f"mtf_strong_downtrend blocks: {len(mtf_blocks)}")

    # Dedup by (token, hour) to avoid re-processing same token multiple times
    seen = set()
    unique_blocks = []
    for e in mtf_blocks:
        tk = e.get("token", "?")
        ts = e.get("ts", "")
        try:
            dt_ = parse_iso(ts)
            hour_key = (tk, dt_.strftime("%Y-%m-%dT%H"))
        except: continue
        if hour_key in seen: continue
        seen.add(hour_key)
        unique_blocks.append(e)
    print(f"Unique (token, hour) pairs: {len(unique_blocks)}")

    # Resolve pairs and fetch forward
    client = DexScreenerClient()
    results = []
    skipped_no_pair = 0
    skipped_no_post = 0
    print(f"Fetching forward outcomes for {len(unique_blocks)} unique blocks ...")
    for i, e in enumerate(unique_blocks):
        tk = e.get("token", "?")
        pair = lookup_pair(tk)
        if not pair:
            skipped_no_pair += 1
            continue
        ts = e.get("ts", "")
        try:
            anchor = parse_iso(ts).timestamp()
        except:
            continue
        post = await fetch_forward(client, pair, anchor)
        if not post:
            skipped_no_post += 1
            continue
        results.append({
            "token": tk,
            "mtf": e.get("mtf"),
            "chart_score": e.get("chart_score"),
            "pc_h24": e.get("pc_h24"),
            "mcap_m": e.get("mcap_m"),
            **post,
        })
        await asyncio.sleep(0.05)
        if (i+1) % 30 == 0:
            print(f"  {i+1}/{len(unique_blocks)} done — resolved={len(results)} no_pair={skipped_no_pair} no_post={skipped_no_post}")

    print(f"\nResults: resolved={len(results)} skipped_no_pair={skipped_no_pair} skipped_no_post={skipped_no_post}")

    if not results:
        print("Nothing to analyze.")
        return

    # Categorize
    winners = [r for r in results if r["peak_above"] >= 5.0 and r["drift_60m"] > 0]
    dumpers = [r for r in results if r["drift_60m"] < -5.0]
    mixed = [r for r in results if r not in winners and r not in dumpers]
    n = len(results)

    print(f"\n=== mtf_strong_downtrend BLOCKS — would-have-won analysis ===")
    print(f"  Total resolved: {n}")
    print(f"  WINNERS (peak>=+5 AND drift>0):  {len(winners)} ({len(winners)/n*100:.0f}%)")
    print(f"  DUMPERS (drift < -5):            {len(dumpers)} ({len(dumpers)/n*100:.0f}%)")
    print(f"  MIXED:                            {len(mixed)} ({len(mixed)/n*100:.0f}%)")

    # Avg metrics
    avg_peak = sum(r["peak_above"] for r in results) / n
    avg_drift = sum(r["drift_60m"] for r in results) / n
    print(f"\n  Avg peak_above_block:  {avg_peak:+.1f}%")
    print(f"  Avg drift_60m:         {avg_drift:+.1f}%")

    print(f"\n=== Per-block detail (top 10 by peak) ===")
    print(f"  {'Token':<14} {'mtf':<13} {'cs':>4} {'pc_h24':>7} {'mcap_m':>7} {'peak':>7} {'drift':>7}")
    for r in sorted(results, key=lambda x: -x["peak_above"])[:15]:
        print(f"  {r['token']:<14} {str(r['mtf']):<13} {r.get('chart_score',0):>4.0f} "
              f"{r['pc_h24']:>+6.0f}% {r.get('mcap_m',0):>6.2f} "
              f"{r['peak_above']:>+6.1f}% {r['drift_60m']:>+6.1f}%")

    # Verdict
    fbr = len(winners) / n
    if fbr >= 0.30:
        verdict = "HIGH false-block rate — filter is over-blocking. Consider loosening or new carve-out."
    elif fbr >= 0.15:
        verdict = "MODERATE false-block rate. Filter mostly right; targeted carve-out could help."
    else:
        verdict = "LOW false-block rate. Filter is doing its job — leave as-is."
    print(f"\n  VERDICT: false-block rate = {fbr*100:.0f}% — {verdict}")

    # Save for re-runs
    with open(".mtf_false_block_audit.json", "w") as f:
        json.dump({"results": results, "verdict": verdict, "false_block_rate": fbr}, f, indent=2)
    print(f"  → saved {len(results)} samples to .mtf_false_block_audit.json")


if __name__ == "__main__":
    asyncio.run(main())
