"""Fill-fidelity audit (2026-06-11): do our paper fills match what the tape
actually printed around our fill moments? Thin-book microcaps are where paper
models flatter — and where smart wallet + the badday family live.

Method: for today's smart_follow fills (buy + sell legs), pull the pair's
DexScreener trade log and compare our recorded fill price to real prints
within ±90s. gap% = ours/market_median - 1 (buys: positive = we modeled a
WORSE fill than tape = conservative; negative = paper flattered us).
"""
import asyncio, json, sys, statistics
from datetime import datetime, timezone
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

tr = json.load(open("_trades_cache.json"))
buy_strat = {}
for t in tr:
    if t.get("type") == "buy" and t.get("strategy"):
        buy_strat[(t.get("pair_address") or t.get("address") or "").lower()] = t["strategy"]

fills = []   # (pair, ts_epoch, side, our_price, token)
for t in tr:
    if (t.get("time") or "") < "2026-06-11T00:00":
        continue
    k = (t.get("pair_address") or t.get("address") or "").lower()
    if not str(buy_strat.get(k, "")).startswith("smart_follow"):
        continue
    pair = t.get("pair_address") or ""
    px = t.get("entry_price") if t.get("type") == "buy" else t.get("exit_price")
    if not isinstance(px, (int, float)) or px <= 0:
        continue
    try:
        ts = datetime.fromisoformat(t["time"].replace("Z", "+00:00")).timestamp()
    except Exception:
        continue
    fills.append((pair or (t.get("address") or ""), ts, t["type"], px, t.get("token")))

fills = fills[-14:]   # bound the run; each pool fetch is throttle-paced
print(f"fills to audit (today, smart_follow): {len(fills)}")

# smart_follow records carry empty pair_address (external-signal path) —
# resolve the top pool per mint via DexScreener (the post-stop-test lesson)
import urllib.request
_pool_cache = {}
def resolve_pool(mint):
    if mint in _pool_cache:
        return _pool_cache[mint]
    try:
        req = urllib.request.Request(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            j = json.loads(r.read())
        ps = [p for p in (j.get("pairs") or []) if p.get("chainId") == "solana"]
        ps.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
        # prefer DEXes whose trade-log endpoint actually serves (meteora/orca 404)
        _ok = [p for p in ps if (p.get("dexId") or "") in ("pumpswap", "raydium", "pumpfun")]
        pick = (_ok or ps)[:1]
        _pool_cache[mint] = pick[0]["pairAddress"] if pick else None
    except Exception:
        _pool_cache[mint] = None
    return _pool_cache[mint]

async def main():
    from feeds.dexscreener_client import DexScreenerClient
    cl = DexScreenerClient()
    logs = {}
    gaps = {"buy": [], "sell": []}
    matched = 0
    import time as _t
    for pair, ts, side, px, tok in fills:
        # records store the MINT in address with empty pair_address — resolve all
        pair = resolve_pool(pair) or ""
        _t.sleep(0.35)
        if not pair:
            continue
        if pair not in logs:
            log = []
            for attempt in range(4):   # throttle-tolerant: empty -> cool off
                try:
                    log = await cl.fetch_recent_trades(pair, limit=200)
                except Exception:
                    log = []
                if log:
                    break
                await asyncio.sleep(25)
            logs[pair] = log
            await asyncio.sleep(8)     # paced — slow data is still data
        prints = []
        for tt in logs[pair]:
            t_ts = tt.get("ts")
            try:
                p_ts = datetime.fromisoformat(str(t_ts).replace("Z", "+00:00")).timestamp() if isinstance(t_ts, str) else float(t_ts)
            except Exception:
                continue
            if abs(p_ts - ts) <= 150 and isinstance(tt.get("price_usd"), (int, float)) and tt["price_usd"] > 0:
                prints.append(tt["price_usd"])
        if len(prints) < 3:
            continue
        matched += 1
        med = statistics.median(prints)
        gap = (px / med - 1) * 100
        lo, hi = min(prints), max(prints)
        inside = lo * 0.995 <= px <= hi * 1.005
        gaps[side].append(gap)
        print(f"  {str(tok)[:10]:10s} {side:4s} ours={px:.3e} tape_med={med:.3e} "
              f"gap={gap:+6.2f}% prints={len(prints)} inside_range={'Y' if inside else 'N'}")
    print(f"\nmatched {matched}/{len(fills)} fills against tape prints (±90s)")
    for side in ("buy", "sell"):
        g = gaps[side]
        if g:
            print(f"{side:4s}: n={len(g)} median gap {statistics.median(g):+.2f}% "
                  f"| mean {statistics.mean(g):+.2f}% | worst-flattering {min(g):+.2f}%")
    print("\nREAD: buys gap>0 = paper charged us more than tape (conservative);")
    print("      sells gap>0 = paper paid us more than tape (FLATTERING).")

asyncio.run(main())
