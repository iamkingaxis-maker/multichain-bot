"""Mine fresh high-catch wallets for QUALITY (bag-adjusted), like our top 3 keepers.

The local discovered dataset is winners-biased: it gives n_winners (how many winning
tokens a wallet caught) but NOT their bags, so it can't measure quality on its own.
A sprayer who buys everything also scores high on n_winners. The ONLY separator is
on-chain: how many of a wallet's CURRENT holdings are dead bags (liq < $1k).

This ranks the top fresh (not-yet-held) wallets by n_winners, then bag-validates them
on-chain, with the 3 proven keepers measured IN THE SAME RUN as a live baseline.

Quality metric: winners_caught vs dead_bags_held. Abk9Efh ref ~ 64 winners / ~63% dead
but high realized sell-through. We want wallets that catch winners WITHOUT an absurd
absolute dead-bag pile (the 8zkgFGV/142-bag trap).

Usage: python scripts/mine_quality_wallets.py [top_n=18]  > out.txt 2>err.txt
"""
from __future__ import annotations
import json, sys, subprocess, time

TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
RPCS = ["https://api.mainnet-beta.solana.com", "https://solana.leorpc.com/?api_key=FREE"]
KEEPERS = {  # measured in-run as the live baseline bar
    "Abk9EfhWsLnxuMm7qJXvMYNyvKgtp8sfSoL45srMzP49": "Abk9Efh",
    "V21GW8PGcWRE5DnbjHZXhcjiBYhJxmjBHqAUkfBm2n9": "V21GW8P",
    "HmP3TxuVWkiJjS6mii9WPzRFeF9hnUjs1YMHCAB4AZm4": "HmP3Txu",
}


def _holdings(addr, tries=5):
    for rpc in RPCS:
        for t in range(tries):
            out = subprocess.run(["curl", "-s", "--max-time", "20", "-X", "POST", rpc,
                "-H", "Content-Type: application/json", "-d", json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
                    "params": [addr, {"programId": TOKEN_PROGRAM}, {"encoding": "jsonParsed"}]})],
                capture_output=True, text=True, errors="replace").stdout
            try:
                v = (json.loads(out).get("result") or {}).get("value")
            except Exception:
                v = None
            if v is not None:
                mints = []
                for a in v:
                    info = a.get("account", {}).get("data", {}).get("parsed", {}).get("info", {}) or {}
                    if float((info.get("tokenAmount", {}) or {}).get("uiAmount") or 0) > 0:
                        m = info.get("mint")
                        if m:
                            mints.append(m)
                return mints
            time.sleep(0.5 * (t + 1))
    return None


def _dead_ratio(mints):
    if not mints:
        return 0.0, 0
    dead = 0
    for i in range(0, len(mints), 30):
        batch = ",".join(mints[i:i + 30])
        out = subprocess.run(["curl", "-s", "--max-time", "20", "-H", "User-Agent: Mozilla/5.0",
            f"https://api.dexscreener.com/latest/dex/tokens/{batch}"],
            capture_output=True, text=True, errors="replace").stdout
        try:
            pairs = json.loads(out).get("pairs") or []
        except Exception:
            pairs = []
        liq = {}
        for p in pairs:
            bt = (p.get("baseToken") or {}).get("address")
            if bt:
                liq[bt] = max(liq.get(bt, 0), float((p.get("liquidity") or {}).get("usd") or 0))
        for m in mints[i:i + 30]:
            if liq.get(m, 0) < 1000:
                dead += 1
        time.sleep(0.8)
    return dead / len(mints), dead


def main():
    topn = int(sys.argv[1]) if len(sys.argv) > 1 else 18
    rec = json.load(open("_prune_mine/discovered_wallets.json"))
    have = set(KEEPERS) | {  # also exclude the 12 wallets already collected/known
        "2tgUbS9UMoQD6GkDZBiqJ8tF", "7JCe3GHwkEr3feHgtLXn", "4jkL4dNkY2UbpYrTgLA3",
        "JD6rVaerbyz6wjQ433nr", "Em8J3gBWapfVBGVhVipw", "AXuRt6qru3Pic3NMzmbk",
        "GxDC9e7SP9mzhDo4re5H", "AgmLJBMDCqWynYnQiPCu", "45Sn4KL1MHqwnp",
    }
    def known(w):
        return any(w.startswith(p[:14]) for p in have)

    cands = sorted(((w, v.get("n_winners", 0)) for w, v in rec.items()
                    if not known(w) and w not in KEEPERS),
                   key=lambda r: -r[1])[:topn]

    # validate keepers first (live baseline), then candidates
    print(f"{'wallet':16s} {'role':10s} {'nwin':>5s} | {'held':>4s} {'dead':>4s} {'dead%':>6s}  verdict", flush=True)
    print("-" * 72, flush=True)

    def run_one(addr, label, role, nwin):
        mints = _holdings(addr)
        if mints is None:
            print(f"  {label:14s} {role:10s} {nwin:5d} | RPC-FAIL", flush=True)
            return None
        dr, dead = _dead_ratio(mints) if mints else (0.0, 0)
        return len(mints), dead, dr

    base = {}
    for addr, lbl in KEEPERS.items():
        r = run_one(addr, lbl, "KEEPER", rec.get(addr, {}).get("n_winners", 0))
        if r:
            base[lbl] = r
            print(f"  {lbl:14s} {'KEEPER':10s} {rec.get(addr,{}).get('n_winners',0):5d} | "
                  f"{r[0]:4d} {r[1]:4d} {r[2]*100:5.0f}%", flush=True)
        time.sleep(0.6)

    # baseline bar = worst keeper dead% (be at least as clean as our weakest keeper)
    bar = max((v[2] for v in base.values()), default=0.65)
    print(f"\n  >> baseline bar (worst keeper dead%): {bar*100:.0f}%\n", flush=True)

    results = []
    for w, nwin in cands:
        r = run_one(w, w[:14], "cand", nwin)
        if r:
            held, dead, dr = r
            # quality: catches winners, dead% not worse than our weakest keeper,
            # and absolute dead pile not absurd (the 142-bag trap)
            verdict = "STRONG" if (dr <= bar and dead <= 60) else (
                "candidate" if (dr <= bar + 0.10 and dead <= 100) else "bagger-skip")
            results.append((w, nwin, held, dead, dr, verdict))
            print(f"  {w[:14]:14s} {'cand':10s} {nwin:5d} | {held:4d} {dead:4d} {dr*100:5.0f}%  {verdict}", flush=True)
        time.sleep(0.6)

    keep = [r for r in results if r[5] in ("STRONG", "candidate")]
    keep.sort(key=lambda r: (r[5] != "STRONG", r[4], -r[1]))
    print(f"\n=== {len(keep)} new candidates clear the keeper bar ===", flush=True)
    for w, nwin, held, dead, dr, v in keep:
        print(f"  {w}  nwin={nwin} dead%={dr*100:.0f} ({dead}/{held})  {v}", flush=True)
    print("\nNote: dead% = current-holdings snapshot. Pair with live follow-tracking "
          "(sell-through) before adding to the K=1 set.", flush=True)


if __name__ == "__main__":
    main()
