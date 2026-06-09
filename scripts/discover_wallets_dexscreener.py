"""Fresh smart-money discovery via DexScreener trade logs (NOT the stored roster).

The stored 8543-wallet roster is winners-biased and may be tapped of new high-value
wallets. This finds NEW ones from scratch:

  1. Pull recent Solana RUNNERS from GeckoTerminal (trending + top-volume pools),
     keep ones that actually pumped recently and are young enough that their EARLY
     buyers are still inside the trade-log window.
  2. For each runner, fetch the DexScreener internal trade log (io.dexscreener.com,
     via feeds.dexscreener_client) and harvest the EARLY buy makers (first buyers,
     min volume to drop dust).
  3. Tally makers across runners. A wallet that was an early buyer on >=MIN_HITS
     distinct runners has selection edge.
  4. Cross-check against the stored roster (_prune_mine/discovered_wallets.json) and
     surface only NEW wallets (and how they rank vs known ones).

New candidates still need on-chain bag-validation (scripts/mine_quality_wallets.py)
before joining the K=1 follow set — this only finds the catch, not the quality.

Usage: python scripts/discover_wallets_dexscreener.py [min_hits=2]  > out.txt 2>err.txt
"""
from __future__ import annotations
import asyncio, json, os, sys, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:  # Windows console defaults to cp1252; token names carry emoji/unicode
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from curl_cffi import requests as cr  # noqa: E402
from feeds.dexscreener_client import DexScreenerClient  # noqa: E402

NOW = datetime.now(timezone.utc)
_S = cr.Session(impersonate="chrome")

# runner = pumped recently AND young enough that early buyers are in the trade window
# WIDE harvest profile (AxiS 2026-06-09): loosen pump/age/liq + more GT pages to pull a
# much larger runner universe in one pass, then forward-track the bigger candidate pool.
MAX_AGE_H = 24.0           # caveat: for high-vol older runners the ~200-trade window may
                           # not reach true launch — "early" then means early-in-window
MIN_LIQ = 15_000           # tradeable, filters rugs/dust
MIN_PUMP_H6 = 20.0         # >=+20% over 6h = a run (loosened from 40)
EARLY_FRAC = 0.35          # earliest 35% of the harvested log = "early" buyers
MIN_BUY_USD = 15.0         # drop dust buys
TRADE_LIMIT = 200          # max trade records per pool (parser cap)


def _gt(url, tries=5):
    for t in range(tries):
        try:
            r = _S.get(url, timeout=30)
            if r.status_code == 200:
                return r.json()
            time.sleep(9 if r.status_code == 429 else 4)
        except Exception:
            time.sleep(5)
    return None


def find_runners():
    urls = ["https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1",
            "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=2",
            "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=3"]
    for pg in range(1, 11):    # top h24-volume pools, pages 1-10
        urls.append(f"https://api.geckoterminal.com/api/v2/networks/solana/pools"
                    f"?sort=h24_volume_usd_desc&page={pg}")
    for pg in range(1, 6):     # top h6-volume = fresher movers
        urls.append(f"https://api.geckoterminal.com/api/v2/networks/solana/pools"
                    f"?sort=h6_volume_usd_desc&page={pg}")
    seen = {}
    for u in urls:
        j = _gt(u)
        if not j:
            print(f"  GT FAIL {u[-30:]}", file=sys.stderr); time.sleep(3.8); continue
        for it in j.get("data", []):
            a = it.get("attributes", {})
            ca = a.get("pool_created_at")
            if not ca:
                continue
            try:
                age_h = (NOW - datetime.fromisoformat(ca.replace("Z", "+00:00"))).total_seconds() / 3600
            except Exception:
                continue
            pcp = a.get("price_change_percentage") or {}
            try:
                h6 = float(pcp.get("h6") or 0); h1 = float(pcp.get("h1") or 0)
            except Exception:
                h6 = h1 = 0.0
            pair = it.get("id", "").replace("solana_", "")
            # base token mint = dedup key so a wallet's "hits" counts DISTINCT TOKENS,
            # not multiple pools of the same token (fixes the inflated >=2 bar)
            try:
                tok = (((it.get("relationships") or {}).get("base_token") or {})
                       .get("data") or {}).get("id", "").replace("solana_", "")
            except Exception:
                tok = ""
            seen[pair] = {"pair": pair, "token": tok or pair, "name": a.get("name", ""),
                          "age_h": age_h, "liq": float(a.get("reserve_in_usd") or 0),
                          "h6": h6, "h1": h1}
        print(f"  GT {u[-34:]}: cum {len(seen)}", file=sys.stderr); time.sleep(3.8)
    runners = [c for c in seen.values()
               if c["age_h"] <= MAX_AGE_H and c["liq"] >= MIN_LIQ and c["h6"] >= MIN_PUMP_H6]
    runners.sort(key=lambda x: -x["h6"])
    return runners


async def harvest(runners):
    client = DexScreenerClient()
    maker_hits = {}          # wallet -> set(token_mint) it was an early buyer on (DISTINCT tokens)
    maker_vol = {}           # wallet -> cumulative early buy $
    for i, c in enumerate(runners):
        try:
            trades = await client.fetch_recent_trades(c["pair"], limit=TRADE_LIMIT)
        except Exception as e:
            print(f"  [{i+1}/{len(runners)}] {c['name'][:18]:18s} ERR {e}", file=sys.stderr)
            trades = []
        buys = [t for t in trades if t.get("kind") == "buy" and t.get("maker")
                and float(t.get("volume_usd") or 0) >= MIN_BUY_USD]
        buys.sort(key=lambda t: t.get("ts", ""))      # oldest first
        n_early = max(1, int(len(buys) * EARLY_FRAC))
        early = buys[:n_early]
        for t in early:
            m = str(t["maker"])
            maker_hits.setdefault(m, set()).add(c["token"])   # dedup by TOKEN, not pool
            maker_vol[m] = maker_vol.get(m, 0.0) + float(t.get("volume_usd") or 0)
        print(f"  [{i+1}/{len(runners)}] {c['name'][:18]:18s} h6=+{c['h6']:.0f}% "
              f"trades={len(trades)} early_buyers={len(early)}", file=sys.stderr)
        await asyncio.sleep(0.4)
    return maker_hits, maker_vol


def main():
    min_hits = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    print("=== Phase 1: find recent Solana runners (GeckoTerminal) ===", file=sys.stderr)
    runners = find_runners()
    print(f"\nrunners (age<={MAX_AGE_H}h, liq>=${MIN_LIQ/1000:.0f}k, h6>=+{MIN_PUMP_H6:.0f}%): {len(runners)}")
    for c in runners[:40]:
        print(f"  {c['name'][:26]:26s} h6=+{c['h6']:6.0f}% h1=+{c['h1']:6.0f}% "
              f"age={c['age_h']:4.1f}h liq=${c['liq']/1000:.0f}k")
    if not runners:
        print("no runners matched — loosen MIN_PUMP_H6 / MAX_AGE_H"); return

    print(f"\n=== Phase 2: harvest early buy-makers from {len(runners)} runner trade logs ===",
          file=sys.stderr)
    maker_hits, maker_vol = asyncio.run(harvest(runners))

    # cross-check against the stored roster
    roster = {}
    try:
        roster = json.load(open("_prune_mine/discovered_wallets.json"))
    except Exception:
        pass

    rows = [(m, len(p), maker_vol.get(m, 0.0), m in roster, roster.get(m, {}).get("n_winners", 0))
            for m, p in maker_hits.items() if len(p) >= min_hits]
    rows.sort(key=lambda r: (-r[1], -r[2]))
    new_rows = [r for r in rows if not r[3]]

    n_tokens = len({c["token"] for c in runners})
    print(f"\n(runners spanned {n_tokens} DISTINCT tokens across {len(runners)} pools)")
    print(f"\n=== makers that were EARLY buyers on >={min_hits} distinct TOKENS: {len(rows)} "
          f"({len(new_rows)} NEW, not in stored roster) ===")
    print(f"{'wallet':46s} {'tokens':>7s} {'early$':>9s}  status")
    for m, hits, vol, known, nwin in rows[:60]:
        status = f"known (roster nwin={nwin})" if known else "*** NEW ***"
        print(f"  {m:44s} {hits:5d}   ${vol:8.0f}  {status}")

    if new_rows:
        out = [{"wallet": m, "runner_hits": h, "early_vol_usd": round(v, 2)}
               for m, h, v, known, nw in new_rows]
        json.dump(out, open("_new_wallet_candidates.json", "w"), indent=2)
        print(f"\nWrote {len(out)} NEW candidates to _new_wallet_candidates.json")
    else:
        print("\nNo new wallets above the hit threshold — roster may genuinely be saturated "
              "at this pump/age cut. Try min_hits=2 or loosen MIN_PUMP_H6.")

    # --- recurrence accumulation: each manual run logs a dated snapshot, so wallets
    # that show up early across MULTIPLE runs surface as the real high-value finds.
    # (One snapshot can't rank wallets — even a proven 115-winner wallet only hits ~2
    #  of a day's runners — so recurrence across runs is the actual quality signal.)
    log_path = "_wallet_discovery_log.json"
    log = {}
    if os.path.exists(log_path):
        try:
            log = json.load(open(log_path))
        except Exception:
            log = {}
    stamp = NOW.strftime("%Y-%m-%dT%H")        # hour-stamped so multiple runs/day don't clobber
    log[stamp] = {m: round(v, 2) for m, h, v, known, nw in new_rows}
    json.dump(log, open(log_path, "w"), indent=2)

    from collections import Counter
    runs = Counter()
    last_vol = {}
    for snap in log.values():
        for w, v in snap.items():
            runs[w] += 1
            last_vol[w] = v
    recur = sorted(((w, n) for w, n in runs.items() if n >= 2), key=lambda r: -r[1])
    print(f"\n=== recurrence log: {len(log)} run(s) recorded -> {os.path.abspath(log_path)} ===")
    print(f"wallets seen early across >=2 runs: {len(recur)}  (THESE are the real candidates)")
    for w, n in recur[:40]:
        print(f"  {w}  seen in {n} runs  last_early=${last_vol.get(w,0):.0f}")
    if not recur:
        print("  (none yet — run this again on future days; recurring wallets accumulate here)")
    print("\nNEXT: once a wallet recurs across several runs, bag-validate + forward-track "
          "before adding to the follow set.")


if __name__ == "__main__":
    main()
