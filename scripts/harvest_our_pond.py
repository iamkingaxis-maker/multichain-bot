"""Harvest early buyers of OUR pond's winners (the fleet's confirmed +pnl tokens).

discover_wallets_dexscreener sources GeckoTerminal TRENDING runners — and the
2026-06-14 decode of that catch showed its early buyers are big fast scalpers,
mostly losing, with 0% overlap with our scanner (an INVISIBLE pond we can't act on).
This sources the tokens the FLEET actually WON on, so the caught wallets overlap our
pond by construction and caught OUR kind of setup -> chameleon-feedable intelligence.

Flow:
  1. Fleet winner tokens from /api/trades (net realized +pnl >= MIN_PNL, recent window).
  2. token mint -> best Solana pool (DexScreener public token API).
  3. early buy-makers per pool trade log (feeds.dexscreener_client, the proven plumbing).
  4. tally makers across winners (>= MIN_HITS distinct winner tokens = selection on OUR setup).
  5. cross-ref roster + the GT-discovery catch (ALSO-GT = early in BOTH ponds = strongest)
     + an hour-stamped recurrence log (one snapshot can't rank; recurrence is the validator).

Caveat: for OLD/high-vol winners the ~200-trade window may not reach true launch, so
"early" there means early-in-window (same caveat as the GT discovery).

Usage: python scripts/harvest_our_pond.py [min_hits=2] [min_pnl=10] > out.txt 2> err.txt
"""
from __future__ import annotations
import asyncio
import gzip
import io
import json
import os
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from feeds.dexscreener_client import DexScreenerClient  # noqa: E402

NOW = datetime.now(timezone.utc)
API = "https://gracious-inspiration-production.up.railway.app"
EARLY_FRAC = 0.35       # earliest 35% of the harvested buy log = "early" buyers
MIN_BUY_USD = 15.0      # drop dust buys
TRADE_LIMIT = 200       # max trade records per pool (parser cap)


def _api_json(url, tries=5):
    """urllib + User-Agent + gzip — works for the Railway API (gzip_middleware) AND
    the DexScreener public REST API (keyless, needs a User-Agent)."""
    for t in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            return json.loads(raw)
        except Exception as e:
            if t == tries - 1:
                print(f"  GET FAIL {url[:54]}: {e}", file=sys.stderr)
            time.sleep(4)
    return None


def fleet_winners(min_pnl):
    """Tokens the fleet realized net +pnl on (recent window) -> {mint: (symbol, pnl)}."""
    j = _api_json(f"{API}/api/trades?limit=800")
    rows = j if isinstance(j, list) else (j or {}).get("trades", [])
    agg = defaultdict(lambda: [0.0, None])   # mint -> [pnl, symbol]
    for t in rows:
        if t.get("type") == "sell" and isinstance(t.get("pnl"), (int, float)):
            mint = t.get("token_address") or t.get("address")
            if not mint:
                continue
            agg[mint][0] += t["pnl"]
            agg[mint][1] = t.get("token") or agg[mint][1]
    return {m: (s, round(p, 2)) for m, (p, s) in agg.items() if p >= min_pnl}


def token_to_pool(mint):
    """Best (highest-liq) Solana pool for a token mint via the DexScreener public API."""
    j = _api_json(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", tries=4)
    if not j:
        return None
    pairs = [p for p in (j.get("pairs") or []) if p.get("chainId") == "solana"]
    if not pairs:
        return None
    pairs.sort(key=lambda p: -float((p.get("liquidity") or {}).get("usd") or 0))
    return pairs[0].get("pairAddress")


async def harvest(winners):
    client = DexScreenerClient()
    maker_hits = defaultdict(set)   # wallet -> set(winner mint) it was an early buyer on
    maker_vol = defaultdict(float)  # wallet -> cumulative early buy $
    resolved = 0
    items = sorted(winners.items(), key=lambda kv: -kv[1][1])
    for i, (mint, (sym, pnl)) in enumerate(items):
        pool = token_to_pool(mint)
        if not pool:
            print(f"  [{i+1}/{len(items)}] {str(sym)[:14]:14s} NO POOL", file=sys.stderr)
            time.sleep(1.0)
            continue
        resolved += 1
        try:
            trades = await client.fetch_recent_trades(pool, limit=TRADE_LIMIT)
        except Exception as e:
            print(f"  [{i+1}/{len(items)}] {str(sym)[:14]:14s} ERR {e}", file=sys.stderr)
            trades = []
        buys = [t for t in trades if t.get("kind") == "buy" and t.get("maker")
                and float(t.get("volume_usd") or 0) >= MIN_BUY_USD]
        buys.sort(key=lambda t: t.get("ts", ""))      # oldest first
        n_early = max(1, int(len(buys) * EARLY_FRAC))
        for t in buys[:n_early]:
            m = str(t["maker"])
            maker_hits[m].add(mint)
            maker_vol[m] += float(t.get("volume_usd") or 0)
        print(f"  [{i+1}/{len(items)}] {str(sym)[:14]:14s} pnl=${pnl:+.0f} "
              f"trades={len(trades)} early_buyers={n_early}", file=sys.stderr)
        await asyncio.sleep(0.4)
    return maker_hits, maker_vol, resolved


def main():
    min_hits = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    min_pnl = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    print("=== Phase 1: fleet winner tokens (OUR pond) ===", file=sys.stderr)
    winners = fleet_winners(min_pnl)
    print(f"\nfleet winners (net pnl >= ${min_pnl:.0f}): {len(winners)}")
    for m, (s, p) in sorted(winners.items(), key=lambda kv: -kv[1][1])[:25]:
        print(f"  {str(s)[:16]:16s} ${p:+8.2f}  {m}")
    if not winners:
        print("no fleet winners in window — loosen min_pnl")
        return

    print(f"\n=== Phase 2: harvest early buyers from {len(winners)} winner pools ===",
          file=sys.stderr)
    maker_hits, maker_vol, resolved = asyncio.run(harvest(winners))

    roster = {}
    try:
        roster = json.load(open("_prune_mine/discovered_wallets.json"))
    except Exception:
        pass
    gt_cands = set()
    try:
        gt_cands = {r["wallet"] for r in json.load(open("_new_wallet_candidates.json"))}
    except Exception:
        pass

    rows = [(m, len(h), maker_vol.get(m, 0.0), m in roster, m in gt_cands)
            for m, h in maker_hits.items() if len(h) >= min_hits]
    rows.sort(key=lambda r: (-r[1], -r[2]))
    print(f"\n(resolved {resolved}/{len(winners)} winner pools)")
    print(f"\n=== makers EARLY on >={min_hits} distinct FLEET-WINNER tokens: {len(rows)} ===")
    print(f"{'wallet':46s} {'wins':>5s} {'early$':>9s}  flags")
    for m, hits, vol, known, gt in rows[:60]:
        flags = []
        if gt:
            flags.append("** ALSO-GT-CATCH **")   # early in BOTH ponds = strongest signal
        if known:
            flags.append("roster")
        print(f"  {m:44s} {hits:5d}   ${vol:8.0f}  {' '.join(flags) if flags else 'NEW'}")

    out = [{"wallet": m, "winner_hits": h, "early_vol_usd": round(v, 2),
            "in_roster": k, "also_gt_catch": g} for m, h, v, k, g in rows]
    json.dump(out, open("_our_pond_candidates.json", "w"), indent=2)
    print(f"\nWrote {len(out)} candidates to _our_pond_candidates.json")

    log_path = "_our_pond_discovery_log.json"
    log = {}
    if os.path.exists(log_path):
        try:
            log = json.load(open(log_path))
        except Exception:
            log = {}
    log[NOW.strftime("%Y-%m-%dT%H")] = {m: h for m, h, v, k, g in rows}
    json.dump(log, open(log_path, "w"), indent=2)
    runs = Counter()
    for snap in log.values():
        for w in snap:
            runs[w] += 1
    recur = sorted(((w, n) for w, n in runs.items() if n >= 2), key=lambda r: -r[1])
    print(f"\n=== recurrence: {len(log)} run(s) -> {len(recur)} wallets early across >=2 runs ===")
    for w, n in recur[:30]:
        print(f"  {w}  seen in {n} runs")
    print("\nNEXT: decode the top (wallet_decode) — these OVERLAP our pond, so unlike the GT "
          "catch they're ACTIONABLE. ALSO-GT-CATCH wallets (early in both ponds) decode FIRST.")


if __name__ == "__main__":
    main()
