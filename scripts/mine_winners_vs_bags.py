"""Unified winners-vs-bags mine (+ lead-wallet signal — they're the same question).

The local discovered_buys is winners-only, so it can't discriminate. Here we COLLECT
buy-structure on-chain for a set of key wallets (capturing BOTH the tokens they won
AND the tokens they bagged), label each token winner/bag, and test which buy-structure
features separate them. The lead-wallet + follow-through structure are the headline
candidate features.

label: token in any wallet's winners-list = WINNER; else = BAG (loser).
features per token (from collected smart buys):
  n_buyers, n_elite, lead_proven (lead wallet is a known lead/elite),
  followers_5m (distinct buyers within 5min after the lead's buy = fast follow-through),
  peak10 (max buyers in any 10-min window), span_min
Caveat: on-chain pull captures RECENT buys (last ~N sigs/wallet); winners-list is
historical, so labeling is approximate at the recent/historical boundary.

Usage: python scripts/mine_winners_vs_bags.py [sigs_per_wallet=60]  > out.txt 2>/dev/null
"""
from __future__ import annotations
import json, sys, time, subprocess, collections, statistics

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
STABLE = {"So11111111111111111111111111111111111111112",
          "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
          "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
RPCS = ["https://api.mainnet-beta.solana.com", "https://solana.leorpc.com/?api_key=FREE"]
# key wallets: lead candidates + elite + current keepers
WALLETS = [
    "2tgUbS9UMoQD6G", "7JCe3GHwkEr3fe", "4jkL4dNkY2UbpY", "JD6rVaerbyz6wj",
    "Em8J3gBWapfVBG", "AXuRt6qru3Pic3", "Abk9EfhWsLnxuM", "V21GW8PGcWRE5D",
    "HmP3TxuVWkiJjS", "GxDC9e7SP9mzhD", "AgmLJBMDCqWynY", "45Sn4KL1MHqwnp",
]
LEAD = {"2tgUbS9U", "7JCe3GH", "4jkL4dN", "JD6rVae", "Em8J3gB", "AXuRt6q"}  # prefixes


def _rpc(method, params, tries=6):
    for rpc in RPCS:
        for t in range(tries):
            out = subprocess.run(["curl", "-s", "--max-time", "20", "-X", "POST", rpc,
                "-H", "Content-Type: application/json",
                "-d", json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})],
                capture_output=True, text=True, errors="replace").stdout
            try:
                d = json.loads(out)
                if "result" in d:
                    return d["result"]
            except Exception:
                pass
            time.sleep(0.4 * (t + 1))
    return None


def wallet_full_addr(prefix, rec):
    return next((w for w in rec if w.startswith(prefix)), prefix)


def collect_buys(addr, limit):
    sigs = _rpc("getSignaturesForAddress", [addr, {"limit": limit}]) or []
    out = []
    for s in sigs:
        sig = s.get("signature"); bt = s.get("blockTime")
        if not sig or s.get("err") or not bt:
            continue
        tx = _rpc("getTransaction", [sig, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}])
        time.sleep(0.12)
        if not tx or not tx.get("meta"):
            continue
        meta = tx["meta"]
        pre = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
               for b in (meta.get("preTokenBalances") or []) if b.get("owner") == addr}
        post = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                for b in (meta.get("postTokenBalances") or []) if b.get("owner") == addr}
        try:
            keys = [k if isinstance(k, str) else k.get("pubkey")
                    for k in tx["transaction"]["message"]["accountKeys"]]
            wi = keys.index(addr); sol_d = (meta["postBalances"][wi] - meta["preBalances"][wi]) / 1e9
        except Exception:
            sol_d = None
        for m in set(list(pre) + list(post)):
            if m in STABLE or sol_d is None:
                continue
            if post.get(m, 0) - pre.get(m, 0) > 0 and sol_d < 0:
                out.append((m, bt, -sol_d))
    return out


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    rec = json.load(open("_prune_mine/discovered_wallets.json"))
    winners = set()
    nwin = {}
    for w, v in rec.items():
        nwin[w] = v.get("n_winners", 0); winners |= set(v.get("winners", []))
    elite = {w for w, n in nwin.items() if n >= 50}

    tok = collections.defaultdict(list)  # mint -> [(wallet_prefix, bt, sol, is_elite)]
    for pref in WALLETS:
        addr = wallet_full_addr(pref, rec)
        try:
            buys = collect_buys(addr, limit)
        except Exception:
            buys = []
        is_el = addr in elite
        for m, bt, sol in buys:
            tok[m].append((pref, bt, sol, is_el))
        print(f"  collected {addr[:10]}: {len(buys)} buys", file=sys.stderr)
        time.sleep(0.3)

    W, B = [], []
    for m, es in tok.items():
        buyers = {e[0] for e in es}
        n_el = len({e[0] for e in es if e[3]})
        times = sorted(e[1] for e in es)
        lead_e = min(es, key=lambda x: x[1])
        lead_proven = any(lead_e[0].startswith(p) for p in LEAD)
        lead_t = lead_e[1]
        followers_5m = len({e[0] for e in es if 0 < e[1] - lead_t <= 300})
        peak = 0
        for t0 in times:
            peak = max(peak, len({es[j][0] for j in range(len(times)) if 0 <= times[j] - t0 <= 600}))
        span = (times[-1] - times[0]) / 60.0 if len(times) > 1 else 0.0
        f = {"n_buyers": len(buyers), "n_elite": n_el, "lead_proven": int(lead_proven),
             "followers_5m": followers_5m, "peak10": peak, "span_min": span}
        (W if m in winners else B).append(f)

    print(f"\ncollected tokens: {len(W)+len(B)} | WINNERS={len(W)} BAGS={len(B)}")
    if not W or not B:
        print("need both classes — adjust wallets/limit"); return
    print(f"\n{'feature':12s} {'WIN med':>8s} {'BAG med':>8s} {'WIN mean':>9s} {'BAG mean':>9s} {'lift':>6s}")
    for k in ["n_buyers", "n_elite", "lead_proven", "followers_5m", "peak10", "span_min"]:
        wv = [f[k] for f in W]; bv = [f[k] for f in B]
        wm, bm = statistics.median(wv), statistics.median(bv)
        wmn, bmn = statistics.mean(wv), statistics.mean(bv)
        lift = (wmn / bmn) if bmn else float("inf")
        print(f"  {k:10s} {wm:8.2f} {bm:8.2f} {wmn:9.2f} {bmn:9.2f} {lift:6.2f}")
    # lead_proven discrimination explicitly
    wlp = sum(f["lead_proven"] for f in W) / len(W); blp = sum(f["lead_proven"] for f in B) / len(B)
    print(f"\nLEAD-WALLET signal: winners with a proven lead first-in: {wlp*100:.0f}% | bags: {blp*100:.0f}%")
    print(f"follow-through: winners median followers_5m={statistics.median([f['followers_5m'] for f in W])} | bags={statistics.median([f['followers_5m'] for f in B])}")


if __name__ == "__main__":
    main()
