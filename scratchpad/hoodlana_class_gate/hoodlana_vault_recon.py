"""Reconstruct HOODLANA PumpSwap pool token-vault balance at our entry window.
Vault: F6KmxYyuMDUUN2YBTGxFCirwaTXCS8TQopRvi2GCQps1
Mint:  C4TFLdu1f2iGmKVv7crWVwQfRLApTgUFupxsvwvApump (supply 1B)
Entry window 2026-07-11 02:20-02:40 UTC (1783736400-1783737600); creation 02:10:50; rug by ~02:45.
Checkpoints every result to hoodlana_recon.json."""
import json, os, sys, bisect

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "..", "rug_forensics"))
import rpc_lib  # noqa: E402

VAULT = "F6KmxYyuMDUUN2YBTGxFCirwaTXCS8TQopRvi2GCQps1"
MINT = "C4TFLdu1f2iGmKVv7crWVwQfRLApTgUFupxsvwvApump"
CREATION = 1783735850
OUT = os.path.join(BASE, "hoodlana_recon.json")

state = {}
if os.path.exists(OUT):
    state = json.load(open(OUT))

def save():
    json.dump(state, open(OUT, "w"), indent=1)

# ---- 1. page all signatures for the vault back to creation ----
if "sigs" not in state:
    allsigs, before = [], None
    for page in range(40):
        batch = rpc_lib.sigs_for(VAULT, limit=1000, before=before)
        if not batch:
            break
        allsigs.extend(batch)
        oldest_bt = batch[-1].get("blockTime")
        print(f"page {page}: +{len(batch)} (total {len(allsigs)}) oldest_bt={oldest_bt}", flush=True)
        if oldest_bt and oldest_bt < CREATION:
            break
        if len(batch) < 1000:
            break
        before = batch[-1]["signature"]
    state["sigs"] = [{"s": x["signature"], "bt": x.get("blockTime"), "err": x.get("err") is not None}
                     for x in allsigs]
    save()

sigs = [x for x in state["sigs"] if x["bt"] and not x["err"]]
sigs.sort(key=lambda x: x["bt"])  # oldest first
bts = [x["bt"] for x in sigs]
print(f"usable sigs: {len(sigs)} span {bts[0] if bts else None}..{bts[-1] if bts else None}", flush=True)

WSOL = "So11111111111111111111111111111111111111112"

def vault_post_balance(tx):
    """(token_bal, wsol_bal) post balances for accounts OWNED by the pool (F6Kmx = pool
    authority; it owns both vaults). Returns (None, None) if pool not touched."""
    meta = tx.get("meta") or {}
    tok = sol = None
    for tb in (meta.get("postTokenBalances") or []):
        if tb.get("owner") != VAULT:
            continue
        amt = float((tb.get("uiTokenAmount") or {}).get("uiAmount") or 0)
        if tb.get("mint") == MINT:
            tok = amt
        elif tb.get("mint") == WSOL:
            sol = amt
    return tok, sol

# ---- 2. sample vault balance at checkpoints ----
CHECKPOINTS = {  # label -> unix ts (latest successful tx AT OR BEFORE ts)
    "t_0215": 1783736100, "t_0220": 1783736400, "t_0225": 1783736700,
    "t_0230": 1783737000, "t_0235": 1783737300, "t_0240": 1783737600,
    "t_0245": 1783737900, "t_0300": 1783738800,
}
state.setdefault("checkpoints", {})
for label, ts in sorted(CHECKPOINTS.items(), key=lambda kv: kv[1]):
    if label in state["checkpoints"] and state["checkpoints"][label].get("vault_tokens") is not None:
        continue
    i = bisect.bisect_right(bts, ts) - 1
    got = None
    tried = 0
    while i >= 0 and tried < 12:  # walk back until a tx actually touching vault token balance
        sg = sigs[i]
        tx = rpc_lib.get_tx(sg["s"])
        tried += 1
        if tx:
            bal, sol = vault_post_balance(tx)
            if bal is not None:
                got = {"sig": sg["s"], "blockTime": sg["bt"], "vault_tokens": bal,
                       "vault_pct_of_1B": round(bal / 1e9 * 100, 2),
                       "vault_wsol": sol}
                break
        i -= 1
    state["checkpoints"][label] = got or {"vault_tokens": None, "note": f"no balance found, tried {tried}"}
    print(label, state["checkpoints"][label], flush=True)
    save()

print(json.dumps(state["checkpoints"], indent=1))
print("rpc stats:", rpc_lib.stats())
