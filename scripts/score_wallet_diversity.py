"""Diversity/selection scorer — the CORRECT picker for smart-money FOLLOW wallets.

net-SOL rewards trading VOLUME, so it surfaces single-token MM/churn bots that have no
transferable entry signal (lesson 2026-06-09). A follow wallet must instead show:
  - DIVERSITY: trades many distinct tokens, not one pool churned 100x
  - SELECTION: the tokens it buys+sells round-trip PROFITABLY (realized win-rate),
    and/or it distributes a diverse set of prior winners (many distinct sells)

Per wallet (over last N sigs) we compute:
  n_distinct       distinct tokens traded (buy or sell)
  top_share        max fraction of swaps on a single token  (high => MM/churn)
  roundtrips       tokens bought AND sold in-window
  realized_wr      fraction of roundtrips that were net-SOL-positive  (= SELECTION)
  net_realized     sum of round-trip SOL P&L
  distinct_sells   distinct tokens sold (distributor signal, e.g. Abk9Efh)

Classification:
  MM_CHURN   n_distinct <= 4 AND top_share >= 0.50         -> REJECT (not a selector)
  SELECTOR   n_distinct >= 8 AND (realized_wr >= 0.55 on >=4 roundtrips
                                  OR distinct_sells >= 8)   -> follow-worthy
  WEAK/MIXED everything else

Validation set built in: scores the current watchlist (known-good selectors) AND the 6
net-SOL MM bots (known-bad) so you can see the scorer separates them.

Usage: python scripts/score_wallet_diversity.py [sigs=60] > out.txt 2> err.txt
"""
from __future__ import annotations
import json, os, sys, time, subprocess, collections, statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

STABLE = {"So11111111111111111111111111111111111111112",
          "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
          "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
try:
    from core.rpc_pool import rpc_pool as _rpc_pool
    RPCS = _rpc_pool()
except Exception:
    RPCS = ["https://api.mainnet-beta.solana.com",
            "https://solana-rpc.publicnode.com",
            "https://solana.drpc.org"]


def _rpc(method, params, tries=2):
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


def analyze(addr, sigs):
    """Return diversity/selection metrics over the last `sigs` transactions."""
    sl = _rpc("getSignaturesForAddress", [addr, {"limit": sigs}]) or []
    # per-token accounting
    tok = collections.defaultdict(lambda: {"buys": 0, "sells": 0, "spent": 0.0, "recv": 0.0})
    swaps = 0
    for s in sl:
        sig = s.get("signature")
        if not sig or s.get("err"):
            continue
        tx = _rpc("getTransaction", [sig, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}])
        time.sleep(0.08)
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
            continue
        # the swapped token = non-stable mint with the largest abs balance change
        deltas = {m: post.get(m, 0) - pre.get(m, 0) for m in set(list(pre) + list(post)) if m not in STABLE}
        deltas = {m: d for m, d in deltas.items() if abs(d) > 0}
        if not deltas:
            continue
        mint = max(deltas, key=lambda m: abs(deltas[m]))
        d = deltas[mint]
        if d > 0 and sol_d < 0:        # buy
            tok[mint]["buys"] += 1; tok[mint]["spent"] += -sol_d; swaps += 1
        elif d < 0 and sol_d > 0:      # sell
            tok[mint]["sells"] += 1; tok[mint]["recv"] += sol_d; swaps += 1
    if swaps == 0:
        return None
    n_distinct = len(tok)
    top_share = max((v["buys"] + v["sells"]) for v in tok.values()) / swaps
    roundtrips = [(m, v) for m, v in tok.items() if v["buys"] >= 1 and v["sells"] >= 1]
    realized = [(v["recv"] - v["spent"]) for _, v in roundtrips]
    wins = sum(1 for r in realized if r > 0)
    realized_wr = (wins / len(roundtrips)) if roundtrips else None
    net_realized = sum(realized)
    distinct_sells = sum(1 for v in tok.values() if v["sells"] >= 1)
    return {"swaps": swaps, "n_distinct": n_distinct, "top_share": top_share,
            "roundtrips": len(roundtrips), "realized_wr": realized_wr,
            "net_realized": net_realized, "distinct_sells": distinct_sells}


def classify(m):
    if m["n_distinct"] <= 4 and m["top_share"] >= 0.50:
        return "MM_CHURN"
    diverse = m["n_distinct"] >= 8
    sel = (m["realized_wr"] is not None and m["roundtrips"] >= 4 and m["realized_wr"] >= 0.55) \
        or (m["distinct_sells"] >= 8)
    if diverse and sel:
        return "SELECTOR"
    return "WEAK"


def _load_addrs(path):
    raw = json.load(open(path))
    out = []
    for x in raw:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict):
            w = x.get("wallet") or x.get("addr")
            if w:
                out.append(w)
    return out


def main():
    sigs = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    # optional: positional path to a candidate file (list of addrs or [{wallet:...}])
    cand_file = next((a for a in sys.argv[2:] if not a.startswith("-")), None)
    if cand_file:
        cands = _load_addrs(cand_file)
        targets = [(a, "candidate") for a in cands]
        print(f"# scoring {len(targets)} candidates from {cand_file}\n", file=sys.stderr)
    else:
        watch = json.load(open("config/follow_watchlist.json"))
        try:
            mmbots = json.load(open("_usable_wallets.json"))   # the 6 net-SOL MM bots
        except Exception:
            mmbots = []
        targets = [(a, "watchlist") for a in watch] + \
                  [(a, "MMbot-set") for a in mmbots if a not in watch]

    print(f"{'wallet':14s} {'src':9s} {'swap':>4s} {'ndist':>5s} {'top%':>5s} "
          f"{'rtrip':>5s} {'rWR':>5s} {'netSOL':>8s} {'dSell':>5s}  CLASS", flush=True)
    print("-" * 92, flush=True)
    rows = []
    for addr, src in targets:
        m = analyze(addr, sigs)
        if m is None:
            print(f"  {addr[:12]:12s} {src:9s} no-swaps/RPC-fail", flush=True); continue
        cls = classify(m)
        wr = f"{m['realized_wr']*100:.0f}%" if m["realized_wr"] is not None else " n/a"
        print(f"  {addr[:12]:12s} {src:9s} {m['swaps']:4d} {m['n_distinct']:5d} "
              f"{m['top_share']*100:4.0f}% {m['roundtrips']:5d} {wr:>5s} "
              f"{m['net_realized']:+8.2f} {m['distinct_sells']:5d}  {cls}", flush=True)
        rows.append((addr, src, m, cls))
        time.sleep(0.3)

    sel = [r for r in rows if r[3] == "SELECTOR"]
    mm = [r for r in rows if r[3] == "MM_CHURN"]
    weak = [r for r in rows if r[3] == "WEAK"]
    # rank selectors: realized_wr (if any) then diversity then net realized
    sel.sort(key=lambda r: (-(r[2]["realized_wr"] or 0), -r[2]["n_distinct"], -r[2]["net_realized"]))
    print(f"\n=== SELECTORS (follow-worthy): {len(sel)} | MM_CHURN (reject): {len(mm)} | WEAK: {len(weak)} ===")
    print("\nSELECTORS ranked:")
    for addr, src, m, _ in sel:
        wr = f"{m['realized_wr']*100:.0f}%" if m["realized_wr"] is not None else "n/a"
        print(f"  {addr}  [{src}] ndist={m['n_distinct']} rWR={wr} netSOL={m['net_realized']:+.2f} dSell={m['distinct_sells']}")
    print("\nMM_CHURN (correctly rejected — single/few-token churn):")
    for addr, src, m, _ in mm:
        print(f"  {addr}  [{src}] ndist={m['n_distinct']} top%={m['top_share']*100:.0f}")

    # validation readout (watchlist-mode only): did we separate watchlist (good)
    # from MMbot-set (bad)? Candidate-file mode has neither cohort -> skip
    # (this line crashed every candidate-file run with UnboundLocalError).
    if not cand_file:
        wl_mm = sum(1 for r in rows if r[1] == "watchlist" and r[3] == "MM_CHURN")
        bot_mm = sum(1 for r in rows if r[1] == "MMbot-set" and r[3] == "MM_CHURN")
        print(f"\nVALIDATION: watchlist flagged MM_CHURN={wl_mm} (want 0); "
              f"MMbot-set flagged MM_CHURN={bot_mm}/{len(mmbots)} (want high).")
    json.dump([{"wallet": r[0], "class": r[3], **r[2]} for r in rows],
              open("_wallet_diversity_scores.json", "w"), indent=2)
    print("wrote _wallet_diversity_scores.json")


if __name__ == "__main__":
    main()
