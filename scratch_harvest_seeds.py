"""Harvest candidate buyer wallets from the 7 runner-token seeds."""
from __future__ import annotations
import asyncio, json, sys, time
from collections import defaultdict
sys.path.insert(0, ".")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from curl_cffi import requests as cr
from feeds.dexscreener_client import DexScreenerClient

S = cr.Session(impersonate="chrome")
TOKENS = [
    "3wc945PgYzJfnJWsukptxiL3JsCur6n5sQ172u34pump",
    "6RSCFPsd7ZgiyCcxmZaHr3soUXREKk8EZmH2rH4Gpump",
    "DtgneYfuPv3Jt8PzfMXYq9opRNU2UVtgrLYFdC4Vpump",
    "FtgKs1pyNhgGZmhywZUmgwtKR9SQnQgD9L1cnqKFpump",
    "Et3nNiuGyhQxwVW3W8pLTvsMXcSKiyogHrNjdr4wpoke",
    "DgNoDybzHie6pi2wRWZy74uaHJbwyUJYewhtwiw1pump",
    "GFziLKWd2JX7XpzGMCL5fpWZfZ2DnbCraPkTkF5upump",
]
STABLE = {"So11111111111111111111111111111111111111112"}


def best_pair(mint):
    for t in range(4):
        try:
            r = S.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=25)
            if r.status_code == 200:
                pairs = r.json().get("pairs") or []
                sol = [p for p in pairs if p.get("chainId") == "solana"]
                if not sol:
                    return None, None
                sol.sort(key=lambda p: -(p.get("liquidity", {}).get("usd") or 0))
                p = sol[0]
                return p.get("pairAddress"), p.get("baseToken", {}).get("symbol")
            time.sleep(6 if r.status_code == 429 else 3)
        except Exception as e:
            print(f"  pair-resolve err {e}", file=sys.stderr); time.sleep(3)
    return None, None


async def main():
    cl = DexScreenerClient()
    hits = defaultdict(set)       # wallet -> set(token)
    buyvol = defaultdict(float)   # wallet -> cum buy $
    nbuys = defaultdict(int)
    for mint in TOKENS:
        pair, sym = best_pair(mint)
        if not pair:
            print(f"{mint[:8]} NO PAIR", file=sys.stderr); continue
        try:
            trades = await cl.fetch_recent_trades(pair, limit=300)
        except Exception as e:
            print(f"{mint[:8]} trade-log ERR {e}", file=sys.stderr); continue
        buys = [t for t in trades if t.get("kind") == "buy" and t.get("maker")
                and float(t.get("volume_usd") or 0) >= 30.0]
        buys.sort(key=lambda t: t.get("ts", ""))
        # take the informed cohort: skip earliest 8% (snipers), take next 50%
        lo = int(len(buys) * 0.08)
        hi = lo + max(1, int(len(buys) * 0.50))
        for t in buys[lo:hi]:
            m = str(t["maker"])
            hits[m].add(mint)
            buyvol[m] += float(t.get("volume_usd") or 0)
            nbuys[m] += 1
        print(f"{sym or mint[:8]:12s} pair={pair[:8]} buys={len(buys)} window={hi-lo}",
              file=sys.stderr)
        await asyncio.sleep(0.4)
    rows = sorted(hits.items(), key=lambda kv: (-len(kv[1]), -buyvol[kv[0]]))
    out = []
    print("\n# candidate buyers (>=1 seed-token hit), ranked by distinct hits then $")
    for w, toks in rows:
        if buyvol[w] < 60:  # drop tiny
            continue
        out.append({"wallet": w, "hits": len(toks), "buy_usd": round(buyvol[w], 1),
                    "nbuys": nbuys[w]})
        print(f"  {w}  hits={len(toks)} buy_usd=${buyvol[w]:.0f} nbuys={nbuys[w]}")
    json.dump(out, open("_seed_buyer_candidates.json", "w"), indent=2)
    print(f"\nwrote {len(out)} -> _seed_buyer_candidates.json")


asyncio.run(main())
