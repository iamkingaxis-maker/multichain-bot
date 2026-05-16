"""Round 7: Mine the broader Solana universe — not just the 79 closed trades.

Pulls trending tokens from DexScreener, fetches 1m candles, identifies
dip-recovery events, computes chart + pair-level features, mines for
compound predictors of recovery.

Why: rounds 1-6 mined only closed bot trades (n=79). Compound triggers
generalize better when mined across thousands of tokens, not 79.

Output: .universe_mine/events.json (events with features + outcomes)
        stdout: top compound predictors
"""
from __future__ import annotations
import asyncio, json, time, sys, os
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from feeds.dexscreener_client import DexScreenerClient


OUT_DIR = Path(".universe_mine")
OUT_DIR.mkdir(exist_ok=True)


def fetch_universe(n_target: int = 500):
    """Pull diverse Solana tokens from DexScreener boosts + Gecko trending + search."""
    tokens = []
    pairs_by_addr = {}  # cache (token_addr -> pair_addr) when we already have the pair
    seen = set()

    sources = [
        ("ds_boost_latest", "https://api.dexscreener.com/token-boosts/latest/v1"),
        ("ds_boost_top",    "https://api.dexscreener.com/token-boosts/top/v1"),
        ("ds_profile",      "https://api.dexscreener.com/token-profiles/latest/v1"),
    ]
    for name, url in sources:
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            for entry in r.json() or []:
                if entry.get("chainId") != "solana":
                    continue
                addr = entry.get("tokenAddress")
                if addr and addr not in seen:
                    seen.add(addr)
                    tokens.append(addr)
            print(f"  {name}: cumulative {len(tokens)}")
        except Exception as e:
            print(f"  {name} err: {e}")

    # Gecko trending pools — gives PAIR address; we'll use it directly to skip the pair-resolution step
    pair_addrs_from_gt = []
    try:
        for page in (1, 2, 3, 4, 5):
            r = requests.get(
                f"https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page={page}",
                timeout=15, headers={"Accept": "application/json"},
            )
            d = r.json()
            for p in d.get("data", []):
                attrs = p.get("attributes", {})
                pair_addr = attrs.get("address")
                if pair_addr:
                    pair_addrs_from_gt.append(pair_addr)
            time.sleep(2.1)  # GT free 30/min
        print(f"  gt_trending pairs: {len(pair_addrs_from_gt)}")
    except Exception as e:
        print(f"  gt err: {e}")

    # Gecko top-pools (more candidates)
    try:
        for page in (1, 2, 3, 4, 5):
            r = requests.get(
                f"https://api.geckoterminal.com/api/v2/networks/solana/pools?page={page}",
                timeout=15, headers={"Accept": "application/json"},
            )
            d = r.json()
            for p in d.get("data", []):
                pair_addr = p.get("attributes", {}).get("address")
                if pair_addr and pair_addr not in pair_addrs_from_gt:
                    pair_addrs_from_gt.append(pair_addr)
            time.sleep(2.1)
        print(f"  gt_top pairs: {len(pair_addrs_from_gt)}")
    except Exception as e:
        print(f"  gt_top err: {e}")

    print(f"Discovered {len(tokens)} unique token addrs + {len(pair_addrs_from_gt)} pair addrs (Gecko)")
    return tokens[:n_target], pair_addrs_from_gt[:n_target]


def fetch_pair_data(addr: str):
    """Pull DexScreener pair details — gives liq, vol, txn counts, priceChange."""
    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{addr}",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        data = r.json()
        pairs = data.get("pairs") or []
        # Pick the highest-liq Solana pair
        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if not sol_pairs:
            return None
        return max(sol_pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
    except Exception as e:
        return None


def compute_pair_features(pair: dict):
    """Extract pair-level features (priceChange, txn ratios, liq, vol)."""
    pc = pair.get("priceChange") or {}
    txns = pair.get("txns") or {}
    vol = pair.get("volume") or {}
    liq = pair.get("liquidity") or {}

    def bs_ratio(window):
        d = txns.get(window) or {}
        b, s = d.get("buys") or 0, d.get("sells") or 0
        if s == 0:
            return None
        return b / s

    feats = {
        "pc_m5": pc.get("m5"),
        "pc_h1": pc.get("h1"),
        "pc_h6": pc.get("h6"),
        "pc_h24": pc.get("h24"),
        "bs_m5": bs_ratio("m5"),
        "bs_h1": bs_ratio("h1"),
        "bs_h6": bs_ratio("h6"),
        "bs_h24": bs_ratio("h24"),
        "buys_h1": (txns.get("h1") or {}).get("buys"),
        "sells_h1": (txns.get("h1") or {}).get("sells"),
        "vol_m5": vol.get("m5"),
        "vol_h1": vol.get("h1"),
        "vol_h6": vol.get("h6"),
        "vol_h24": vol.get("h24"),
        "liq_usd": float(liq.get("usd") or 0),
        "fdv": pair.get("fdv") or 0,
        "mcap": pair.get("marketCap") or 0,
        "age_hours": (
            (time.time() * 1000 - (pair.get("pairCreatedAt") or 0)) / 3_600_000
            if pair.get("pairCreatedAt") else None
        ),
    }
    return feats


async def fetch_candles_1m(client: DexScreenerClient, pair_addr: str, limit: int = 180):
    """Fetch 1m candles via DexScreener internal API. 180 = last 3h."""
    try:
        candles = await client.fetch_1m(pair_addr, limit=limit)
        return candles
    except Exception as e:
        return []


def find_dip_events(candles: list, lookback_5m: int = 6, dip_pct: float = -4.0, recovery_pct: float = 2.0):
    """Scan 1m candles for dip events — NO recovery filter (honest evaluation).

    Event def: 5m cumulative drop <= dip_pct. We measure forward outcome
    from this candle's CLOSE — same view the bot has at decision time.
    """
    if len(candles) < lookback_5m + 5:
        return []
    events = []
    for i in range(lookback_5m, len(candles) - 5):
        prev_close = candles[i - lookback_5m].close
        cur_close = candles[i].close
        cum_pct = (cur_close - prev_close) / prev_close * 100 if prev_close > 0 else 0
        if cum_pct > dip_pct:
            continue
        # Avoid clustering
        if events and i - events[-1]["candle_idx"] < 10:
            continue
        # Outcome: max gain in next 30min (peak vs entry close)
        next_window = candles[i + 1: i + 31]
        if not next_window:
            continue
        peak_close = max(c.close for c in next_window)
        peak_pct = (peak_close - cur_close) / cur_close * 100 if cur_close > 0 else 0
        # Realized outcome at +25 min
        end_idx = min(i + 25, len(candles) - 1)
        end_pct = (candles[end_idx].close - cur_close) / cur_close * 100 if cur_close > 0 else 0
        events.append({
            "candle_idx": i,
            "cum_pct_to_dip": cum_pct,
            "low_at_event": candles[i].low,
            "entry_close": cur_close,
            "peak_pct_next_30m": peak_pct,
            "exit_pct_25m": end_pct,
            "won": end_pct > 0,
            "won_5pct": peak_pct >= 5,
            "won_10pct": peak_pct >= 10,
            # Candle-derived features at entry
            "vol_at_event": candles[i].volume,
            "vol_prev3_avg": sum(c.volume for c in candles[i - 3: i]) / 3 if i >= 3 else 0,
            "vol_prev15_avg": sum(c.volume for c in candles[i - 15: i]) / 15 if i >= 15 else 0,
            "body_pct": (candles[i].close - candles[i].open) / candles[i].open * 100 if candles[i].open > 0 else 0,
            "range_pct": (candles[i].high - candles[i].low) / candles[i].low * 100 if candles[i].low > 0 else 0,
            "lower_wick_ratio": (candles[i].open - candles[i].low) / max(candles[i].high - candles[i].low, 1e-9) if candles[i].open > candles[i].close else (candles[i].close - candles[i].low) / max(candles[i].high - candles[i].low, 1e-9),
        })
    return events


async def main():
    tokens, pair_addrs = fetch_universe(n_target=500)
    print(f"Mining {len(tokens)} token-addrs + {len(pair_addrs)} pair-addrs...")

    client = DexScreenerClient(rate_per_min=120)
    all_events = []
    success = 0
    skipped = 0
    items = [("token", a) for a in tokens] + [("pair", a) for a in pair_addrs]

    for i, (kind, addr) in enumerate(items):
        if i % 25 == 0:
            print(f"  [{i}/{len(items)}] success={success} skipped={skipped} events={len(all_events)}")

        # Resolve to a pair
        if kind == "token":
            pair = fetch_pair_data(addr)
            if not pair:
                skipped += 1
                continue
            pair_addr = pair.get("pairAddress")
            token_addr = addr
            symbol = (pair.get("baseToken") or {}).get("symbol")
        else:
            # Already have pair address; fetch pair info to get features
            try:
                r = requests.get(
                    f"https://api.dexscreener.com/latest/dex/pairs/solana/{addr}",
                    timeout=15, headers={"User-Agent": "Mozilla/5.0"},
                )
                data = r.json() or {}
                pairs = data.get("pairs") or data.get("pair") or []
                if isinstance(pairs, dict):
                    pair = pairs
                else:
                    pair = pairs[0] if pairs else None
            except Exception:
                pair = None
            if not pair:
                skipped += 1
                continue
            pair_addr = addr
            token_addr = (pair.get("baseToken") or {}).get("address")
            symbol = (pair.get("baseToken") or {}).get("symbol")

        if not pair_addr:
            skipped += 1
            continue
        pair_feats = compute_pair_features(pair)
        # Relaxed gates — broader universe sample
        if (pair_feats["liq_usd"] or 0) < 20_000:
            skipped += 1
            continue
        if (pair_feats["vol_h24"] or 0) < 50_000:
            skipped += 1
            continue

        # Fetch candles
        candles = await fetch_candles_1m(client, pair_addr, limit=240)  # 4h history
        if not candles or len(candles) < 30:
            skipped += 1
            continue
        events = find_dip_events(candles, lookback_5m=6, dip_pct=-4.0, recovery_pct=2.0)
        for ev in events:
            ev["token_address"] = token_addr
            ev["symbol"] = symbol
            ev.update(pair_feats)
            all_events.append(ev)
        success += 1
        await asyncio.sleep(0.03)

    print(f"\nDone. {success} tokens mined, {len(all_events)} dip-recovery events found.")

    # Persist
    out_file = OUT_DIR / "events.json"
    with out_file.open("w") as f:
        json.dump(all_events, f, default=str)
    print(f"Events written to {out_file}")

    # Quick analysis
    if all_events:
        wins = sum(1 for e in all_events if e["won"])
        wins_5 = sum(1 for e in all_events if e["won_5pct"])
        wins_10 = sum(1 for e in all_events if e["won_10pct"])
        print(f"\nBaseline outcomes:")
        print(f"  WR @ +25min exit:  {wins}/{len(all_events)} = {wins/len(all_events):.0%}")
        print(f"  Peak >=+5% in 30m:  {wins_5}/{len(all_events)} = {wins_5/len(all_events):.0%}")
        print(f"  Peak >=+10% in 30m: {wins_10}/{len(all_events)} = {wins_10/len(all_events):.0%}")


if __name__ == "__main__":
    asyncio.run(main())
