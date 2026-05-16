"""For the 16 untraded CONTINUED tokens, fetch forward 60-min price
and compute what we'd have realized if we'd traded each.

Bucketing:
  - Winner (peak >= +5 AND drift_60m > 0): we missed upside
  - Dumper (drift_60m < -5): correct to skip
  - Mixed: ambiguous
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from feeds.dexscreener_client import DexScreenerClient

DASHBOARD_URL = "https://gracious-inspiration-production.up.railway.app"
WINDOW_S = 60 * 60  # 60 min forward


def parse_iso(s):
    s = s.replace("Z", "+00:00") if "Z" in s else s
    return datetime.fromisoformat(s)


# From continued_to_trade_gap.py output — untraded tokens
UNTRADED = ['HANTA', 'BMT', 'SUMMER', 'HELMUTPAY', 'GAPLA', 'BALLSACKDORKL',
            'Tiles', 'TRUMPLY', 'BABYRAGE', 'BEPE', 'RABBIT', 'VILLAGEBOY',
            'IDLE', 'Fartcoin', 'OKGUY', 'gnosis']


_UNIVERSE_PAIRS = None

def _load_universe_pairs():
    """Build {symbol: pair_address} index from universe_fresh.json."""
    global _UNIVERSE_PAIRS
    if _UNIVERSE_PAIRS is not None:
        return _UNIVERSE_PAIRS
    try:
        data = json.loads(Path("universe_fresh.json").read_text())
    except Exception:
        _UNIVERSE_PAIRS = {}
        return _UNIVERSE_PAIRS
    by_symbol = {}
    for e in data:
        sym = e.get("symbol")
        pair = e.get("pair_address")
        if sym and pair and sym not in by_symbol:
            by_symbol[sym] = pair
    _UNIVERSE_PAIRS = by_symbol
    return by_symbol


def _load_trade_pairs():
    """Build {symbol: pair_address} from our trade history."""
    try:
        with urllib.request.urlopen(f"{DASHBOARD_URL}/api/trades?limit=2000") as r:
            trades = json.loads(r.read())
    except Exception:
        return {}
    out = {}
    for t in trades:
        sym = t.get("token")
        pair = t.get("pair_address")
        if sym and pair and sym not in out:
            out[sym] = pair
    return out


async def find_pair_address(client, token_symbol):
    """Look up pair_address from universe data or trade history (cached)."""
    u = _load_universe_pairs()
    if token_symbol in u:
        return u[token_symbol]
    t = _load_trade_pairs()
    if token_symbol in t:
        return t[token_symbol]
    return None


async def fetch_forward(client, pair, anchor_ts):
    try:
        candles = await client.fetch_1m(pair, limit=200)
    except Exception:
        return None
    pre = [c for c in candles if c.open_time <= anchor_ts]
    post = [c for c in candles if anchor_ts < c.open_time <= anchor_ts + WINDOW_S]
    if not pre or not post:
        return None
    anchor = pre[-1].close
    if anchor <= 0:
        return None
    max_h = max(c.high for c in post)
    min_l = min(c.low for c in post)
    last = post[-1].close
    return {
        "peak_above": (max_h / anchor - 1) * 100,
        "min_below": (min_l / anchor - 1) * 100,
        "drift_60m": (last / anchor - 1) * 100,
        "n_post_candles": len(post),
    }


async def main():
    # Pull signal events to find the first CONTINUED ts per untraded token
    import urllib.parse  # for find_pair_address
    globals()["urllib"].parse = urllib.parse
    with urllib.request.urlopen(f"{DASHBOARD_URL}/api/signal-events?limit=2000") as r:
        events = json.loads(r.read())
    events = events if isinstance(events, list) else events.get("events", events.get("rows", []))
    cont = [e for e in events if e.get("outcome") == "CONTINUED"]

    # For each untraded token, find the FIRST continued ts
    first_ts = {}
    for e in cont:
        tk = e.get("token", "?")
        if tk not in UNTRADED: continue
        ts = e.get("ts", "")
        if tk not in first_ts or ts < first_ts[tk]:
            first_ts[tk] = ts

    client = DexScreenerClient()
    results = []
    print(f"Fetching forward outcomes for {len(first_ts)} untraded tokens ...")
    for tk, ts in first_ts.items():
        pair = await find_pair_address(client, tk)
        if not pair:
            print(f"  {tk:<14}  NO PAIR FOUND (skip)")
            continue
        anchor_ts = parse_iso(ts).timestamp()
        post = await fetch_forward(client, pair, anchor_ts)
        if not post:
            print(f"  {tk:<14}  NO POST CANDLES (token may have died)")
            results.append({"token": tk, "outcome": "died_or_no_candles"})
            continue
        results.append({"token": tk, "ts": ts, **post})
        await asyncio.sleep(0.1)

    # Categorize
    winners = [r for r in results if r.get("peak_above", -99) >= 5
               and r.get("drift_60m", -99) > 0]
    dumpers = [r for r in results if r.get("drift_60m", 99) < -5]
    mixed = [r for r in results
             if r not in winners and r not in dumpers
             and "peak_above" in r]
    died = [r for r in results if r.get("outcome") == "died_or_no_candles"]

    print(f"\n=== Forward outcomes (60-min window post first CONTINUED signal) ===")
    print(f"  Winners (peak>=+5 AND drift_60m>0):  {len(winners)}/{len(results)}")
    print(f"  Dumpers (drift_60m < -5):            {len(dumpers)}/{len(results)}")
    print(f"  Mixed:                               {len(mixed)}/{len(results)}")
    print(f"  Died/no-candles:                     {len(died)}/{len(results)}")

    print(f"\n  Per-token detail:")
    print(f"  {'Token':<14} {'category':<10} {'peak_above':>10} {'min_below':>10} {'drift_60m':>10}")
    for r in sorted(results, key=lambda x: -(x.get("peak_above") or -99)):
        if r.get("outcome") == "died_or_no_candles":
            print(f"  {r['token']:<14} {'DIED':<10}")
            continue
        cat = "WIN" if r in winners else ("DUMP" if r in dumpers else "MIX")
        print(f"  {r['token']:<14} {cat:<10} {r['peak_above']:>+8.1f}% {r['min_below']:>+8.1f}% {r['drift_60m']:>+8.1f}%")

    if winners:
        print(f"\n  WINNERS MISSED — avg peak_above: {sum(r['peak_above'] for r in winners)/len(winners):+.1f}%")
        print(f"  These tokens passed our filters AND had triggers, but our bot didn't buy")
        print(f"  → next session: investigate cooldown/concurrency caps blocking these")


if __name__ == "__main__":
    asyncio.run(main())
