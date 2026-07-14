"""Extract per-token trips (full mint, first-buy unix ts, return%) for winner wallets.
Output JSON -> scratch_trips.json for the mtf-downtrend OHLC reconstruction step."""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score_wallet_diversity as swd

WALLETS = {
    "C3zP":   "C3zPgDxqJYN4Gf9RotKuHTbBMfpKEsAFSY7hSbFWdSdQ",
    "Zsp75":  "Zsp75pPCt115MgZZwAgmKyDq8vZ94cjBNnLPhwJespQ",
    "B1zhrW": "B1zhrWqDoJdZ9paihqcLN3UY1LzXxxch1zLBzgoaQnKz",
    "2tYcX":  "2tYcXQCfTtQg5dKLEpQn26DjrfhTXhGB8Rh1oiv69DJc",
    "GD856":  "GD856rGyA9PtURL8C1iYwU2rzWVv7ozcCNuNfW3CdV1g",
    "7EWEM":  "7EWEMHqf261xqMga68dHxZX8ifBHEJD5NksxSENfj13",
}
SIGS = int(sys.argv[1]) if len(sys.argv) > 1 else 200

def trade_map(addr, sigs):
    sl = swd._rpc("getSignaturesForAddress", [addr, {"limit": sigs}]) or []
    import collections
    tok = collections.defaultdict(lambda: {"spent":0.0,"recv":0.0,"buys":[],"sells":[]})
    for s in sl:
        sig, bt = s.get("signature"), s.get("blockTime")
        if not sig or s.get("err") or not bt:
            continue
        tx = swd._rpc("getTransaction", [sig, {"maxSupportedTransactionVersion":0,"encoding":"jsonParsed"}])
        time.sleep(0.05)
        if not tx or not tx.get("meta"):
            continue
        meta = tx["meta"]
        pre = {b.get("mint"):float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
               for b in (meta.get("preTokenBalances") or []) if b.get("owner")==addr}
        post = {b.get("mint"):float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                for b in (meta.get("postTokenBalances") or []) if b.get("owner")==addr}
        try:
            keys = [k if isinstance(k,str) else k.get("pubkey") for k in tx["transaction"]["message"]["accountKeys"]]
            wi = keys.index(addr); sol_d = (meta["postBalances"][wi]-meta["preBalances"][wi])/1e9
        except Exception:
            continue
        deltas = {m:post.get(m,0)-pre.get(m,0) for m in set(list(pre)+list(post)) if m not in swd.STABLE}
        deltas = {m:d for m,d in deltas.items() if abs(d)>0}
        if not deltas:
            continue
        mint = max(deltas, key=lambda m:abs(deltas[m])); d = deltas[mint]
        if d>0 and sol_d<0:
            tok[mint]["buys"].append((bt,-sol_d)); tok[mint]["spent"]+=-sol_d
        elif d<0 and sol_d>0:
            tok[mint]["sells"].append((bt,sol_d)); tok[mint]["recv"]+=sol_d
    return tok

out = {}
for name, addr in WALLETS.items():
    print(f"[{name}] decoding {addr[:8]} ...", flush=True)
    tok = trade_map(addr, SIGS)
    trips = []
    for m, r in tok.items():
        if not r["buys"]:
            continue
        b0 = min(b[0] for b in r["buys"])
        closed = bool(r["sells"])
        ret = (r["recv"]/r["spent"]-1)*100 if (closed and r["spent"]) else None
        trips.append({"mint":m, "buy_ts":b0, "ret":ret, "closed":closed,
                      "spent_sol":round(r["spent"],3), "n_buys":len(r["buys"])})
    out[name] = {"addr":addr, "n_tokens":len(trips),
                 "n_closed":sum(1 for t in trips if t["closed"]), "trips":trips}
    print(f"   {len(trips)} tokens, {out[name]['n_closed']} closed", flush=True)

json.dump(out, open("scratch_trips.json","w"), indent=1)
print("WROTE scratch_trips.json")
