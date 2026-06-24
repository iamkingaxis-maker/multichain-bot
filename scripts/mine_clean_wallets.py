"""Mine for more 'Abk9Efh-like' wallets — candidates to add to the K=1 follow set.

Two-phase:
  PHASE 1 (local, fast): from the discovered-wallet dataset, find wallets whose set of
    WINNING tokens overlaps most with our proven keepers' winners. High overlap = the
    wallet keeps catching the same winners our proven wallets caught (similar selection edge).
  PHASE 2 (--validate, RPC+DexScreener): bag-adjusted check on the top candidates — count
    currently-held DEAD positions (the survivorship trap that sank 8zkgFGV/dmuXAmc). A real
    Abk9Efh-like has a tolerable dead-bag ratio, not 60-80%.

Usage:
  python scripts/mine_clean_wallets.py                 # phase 1 only (ranked candidates)
  python scripts/mine_clean_wallets.py --validate 12   # + bag-validate top 12 via RPC
"""
from __future__ import annotations
import json, sys, collections, subprocess, time

KEEPERS = [  # current bag-adjusted keepers (K=1 follow set)
    "Abk9EfhWsLnxuMm7qJXvMYNyvKgtp8sfSoL45srMzP49",
    "V21GW8PGcWRE5DnbjHZXhcjiBYhJxmjBHqAUkfBm2n9",
    "HmP3TxuVWkiJjS6mii9WPzRFeF9hnUjs1YMHCAB4AZm4",
]
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
RPCS = ["https://api.mainnet-beta.solana.com", "https://solana.leorpc.com/?api_key=FREE"]


def _holdings(addr):
    """(total_accounts, [nonzero_mints]) or (None, None)."""
    for rpc in RPCS:
        out = subprocess.run(["curl", "-s", "--max-time", "20", "-X", "POST", rpc,
            "-H", "Content-Type: application/json", "-d", json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
                "params": [addr, {"programId": TOKEN_PROGRAM}, {"encoding": "jsonParsed"}]})],
            capture_output=True, text=True, errors="replace").stdout
        try:
            v = (json.loads(out).get("result") or {}).get("value")
        except Exception:
            continue
        if v is None:
            continue
        mints = []
        for a in v:
            info = a.get("account", {}).get("data", {}).get("parsed", {}).get("info", {}) or {}
            if float((info.get("tokenAmount", {}) or {}).get("uiAmount") or 0) > 0:
                mints.append(info.get("mint"))
        return len(v), [m for m in mints if m]
    return None, None


def _dead_ratio(mints):
    """fraction of held mints with liquidity < $1k (dead bags)."""
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
    rec = json.load(open("_prune_mine/discovered_wallets.json"))
    # target = union of keepers' winning tokens
    target = set()
    for k in KEEPERS:
        target |= set((rec.get(k, {}) or {}).get("winners", []))
    print(f"keeper winning-token universe: {len(target)} tokens")
    if not target:
        print("no keeper winners in dataset — abort"); return

    exclude = set(KEEPERS)
    rows = []
    for w, v in rec.items():
        if w in exclude:
            continue
        wins = set(v.get("winners", []))
        ov = len(wins & target)
        if ov >= 5:  # caught >=5 of the same winners
            rows.append((w, ov, len(wins), v.get("n_winners", 0)))
    rows.sort(key=lambda r: -r[1])
    print(f"\nPHASE 1 — top co-winner candidates (overlap with keepers' winners):")
    print(f"{'wallet':16s} overlap own_winners n_winners")
    for w, ov, nw, nwr in rows[:25]:
        print(f"  {w[:14]:14s} {ov:4d}   {nw:5d}     {nwr}")

    if "--validate" in sys.argv:
        try:
            topn = int(sys.argv[sys.argv.index("--validate") + 1])
        except Exception:
            topn = 12
        print(f"\nPHASE 2 — bag-adjusted validation of top {topn} (RPC + DexScreener):")
        print(f"{'wallet':16s} overlap | held dead dead%  verdict")
        for w, ov, nw, nwr in rows[:topn]:
            tot, mints = _holdings(w)
            if mints is None:
                print(f"  {w[:14]:14s} {ov:4d}   | RPC-FAIL"); continue
            dr, dead = _dead_ratio(mints) if mints else (0.0, 0)
            # Abk9Efh ref = 63% dead but high sell-through; flag <=50% dead as cleaner candidates
            verdict = "CANDIDATE" if dr <= 0.50 else ("borderline" if dr <= 0.65 else "bagger-skip")
            print(f"  {w[:14]:14s} {ov:4d}   | {len(mints):3d} {dead:3d} {dr*100:4.0f}%  {verdict}")
            time.sleep(0.5)
        print("\nNote: dead% is current-holdings only; pair with live follow-tracking before adding.")


if __name__ == "__main__":
    main()
