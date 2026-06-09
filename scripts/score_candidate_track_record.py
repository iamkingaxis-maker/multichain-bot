"""Score discovered candidate wallets by their REALIZED on-chain track record.

One snapshot can't rank wallets (proven: even a 115-winner wallet hits only ~2 of a
day's runners). Holdings dead% is useless on thin wallets. The decisive question is
simpler and measurable NOW: does the wallet actually MAKE MONEY?

For each candidate, pull recent transactions and sum SOL spent on buys vs SOL received
on sells over the window. Net-SOL-positive + actively trading = usable. One-shot /
inactive / net-negative wallets are dropped. The 3 proven keepers run through the same
test as a live baseline.

Usage: python scripts/score_candidate_track_record.py [top_n=25] [sigs=60] > out.txt 2> err.txt
"""
from __future__ import annotations
import json, os, sys, time, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

STABLE = {"So11111111111111111111111111111111111111112",
          "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
          "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
RPCS = ["https://solana.leorpc.com/?api_key=FREE", "https://api.mainnet-beta.solana.com"]
KEEPERS = {
    "Abk9EfhWsLnxuMm7qJXvMYNyvKgtp8sfSoL45srMzP49": "Abk9Efh",
    "V21GW8PGcWRE5DnbjHZXhcjiBYhJxmjBHqAUkfBm2n9": "V21GW8P",
    "HmP3TxuVWkiJjS6mii9WPzRFeF9hnUjs1YMHCAB4AZm4": "HmP3Txu",
}


def _rpc(method, params, tries=2):
    # fail-fast: short timeout + few retries so a throttled getTransaction doesn't hang
    for rpc in RPCS:
        for t in range(tries):
            out = subprocess.run(["curl", "-s", "--max-time", "8", "-X", "POST", rpc,
                "-H", "Content-Type: application/json",
                "-d", json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})],
                capture_output=True, text=True, errors="replace").stdout
            try:
                d = json.loads(out)
                if "result" in d:
                    return d["result"]
            except Exception:
                pass
            time.sleep(0.25)
    return None


def track_record(addr, sigs):
    """Return dict: n_tx, n_swaps, buys, sells, sol_in (spent), sol_out (received), net_sol."""
    sl = _rpc("getSignaturesForAddress", [addr, {"limit": sigs}]) or []
    n_tx = len(sl)
    buys = sells = 0
    sol_spent = sol_recv = 0.0
    parsed = 0
    for s in sl:
        sig = s.get("signature")
        if not sig or s.get("err"):
            continue
        tx = _rpc("getTransaction", [sig, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}])
        time.sleep(0.1)
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
            wi = keys.index(addr)
            sol_d = (meta["postBalances"][wi] - meta["preBalances"][wi]) / 1e9
        except Exception:
            continue
        parsed += 1
        # classify: non-stable token balance up + SOL down = buy; token down + SOL up = sell
        tok_up = any((post.get(m, 0) - pre.get(m, 0)) > 0 for m in set(list(pre) + list(post)) if m not in STABLE)
        tok_dn = any((post.get(m, 0) - pre.get(m, 0)) < 0 for m in set(list(pre) + list(post)) if m not in STABLE)
        if tok_up and sol_d < 0:
            buys += 1; sol_spent += -sol_d
        elif tok_dn and sol_d > 0:
            sells += 1; sol_recv += sol_d
    return {"n_tx": n_tx, "parsed": parsed, "buys": buys, "sells": sells,
            "sol_spent": sol_spent, "sol_recv": sol_recv, "net_sol": sol_recv - sol_spent}


def main():
    topn = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    sigs = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    cands = json.load(open("_new_wallet_candidates.json"))
    cands.sort(key=lambda c: -c.get("early_vol_usd", 0))
    cands = cands[:topn]

    print(f"{'wallet':14s} {'role':7s} {'ntx':>4s} {'buy':>4s} {'sell':>4s} "
          f"{'spent':>8s} {'recv':>8s} {'netSOL':>8s}  flag", flush=True)
    print("-" * 80, flush=True)

    def show(addr, lbl, role):
        r = track_record(addr, sigs)
        if r is None:
            print(f"  {lbl:12s} {role:7s} RPC-FAIL", flush=True); return None
        # usable = actively trading (enough swaps + sells = realizes) AND net-SOL-positive
        active = (r["buys"] + r["sells"]) >= 6 and r["sells"] >= 2
        flag = ("USABLE" if (active and r["net_sol"] > 0) else
                ("net-neg" if active else "inactive/one-shot"))
        print(f"  {lbl:12s} {role:7s} {r['n_tx']:4d} {r['buys']:4d} {r['sells']:4d} "
              f"{r['sol_spent']:8.2f} {r['sol_recv']:8.2f} {r['net_sol']:+8.2f}  {flag}", flush=True)
        return (addr, lbl, role, r, flag)

    base = []
    for addr, lbl in KEEPERS.items():
        x = show(addr, lbl, "KEEPER")
        if x:
            base.append(x)
        time.sleep(0.3)
    print("", flush=True)

    rows = []
    for c in cands:
        x = show(c["wallet"], c["wallet"][:12], "cand")
        if x:
            rows.append(x)
        time.sleep(0.3)

    usable = [r for r in rows if r[4] == "USABLE"]
    usable.sort(key=lambda r: -r[3]["net_sol"])
    print(f"\n=== {len(usable)}/{len(rows)} candidates are USABLE (active + net-SOL-positive) ===", flush=True)
    for addr, lbl, role, r, flag in usable:
        print(f"  {addr}  netSOL={r['net_sol']:+.2f} buys={r['buys']} sells={r['sells']} ntx={r['n_tx']}",
              flush=True)
    if usable:
        json.dump([r[0] for r in usable], open("_usable_wallets.json", "w"), indent=2)
        print(f"\nWrote {len(usable)} usable wallets to _usable_wallets.json", flush=True)
    else:
        print("\nNone passed this window — widen sigs or top_n, or these are early-stage wallets "
              "whose edge isn't realized yet.", flush=True)


if __name__ == "__main__":
    main()
