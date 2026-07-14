"""Step 2/3: per-mint ACTOR features for cohort grading. Checkpoints every token.
Features (all visible at/near buy time):
  deployer            = fee payer of genesis tx (oldest sig of the mint)
  dep_lifetime_sigs   = deployer signature count (cap 1000) -> wallet activity
  dep_first_bt        = deployer's earliest blocktime
  launch_bt           = mint genesis blocktime
  dep_age_at_launch_s = launch_bt - dep_first_bt  (fresh throwaway if small)
  dep_sniped_own      = deployer among first 30 signers of the mint (dev buys own launch)
  reached_genesis     = did we page all the way back (else deployer is approximate)
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import rpc_lib as R

OUT = os.path.join(os.path.dirname(__file__), "actor_features.json")

def fee_payer(tx):
    try:
        k = tx["transaction"]["message"]["accountKeys"][0]
        return k["pubkey"] if isinstance(k, dict) else k
    except Exception:
        return None

def feature_for(mint):
    sigs, hit = R.oldest_sigs(mint, max_pages=5, page=1000)
    if not sigs:
        return {"error": "no_sigs"}
    genesis = sigs[-1]
    launch_bt = genesis.get("blockTime")
    tx = R.get_tx(genesis["signature"])
    dep = fee_payer(tx) if tx else None
    early_signers = []
    for s in sigs[-30:]:
        early_signers.append(s["signature"])
    # deployer lifetime + age
    dep_sigs = R.sigs_for(dep, limit=1000) if dep else []
    dep_first_bt = dep_sigs[-1].get("blockTime") if dep_sigs else None
    # sniped own launch: check the first ~12 txs' fee payers for the deployer
    sniped = False
    snipe_checked = 0
    for s in sigs[-12:]:
        t = R.get_tx(s["signature"])
        snipe_checked += 1
        if t and fee_payer(t) == dep and s["signature"] != genesis["signature"]:
            sniped = True
            break
    age = (launch_bt - dep_first_bt) if (launch_bt and dep_first_bt) else None
    return {
        "deployer": dep,
        "reached_genesis": hit,
        "mint_sigs_seen": len(sigs),
        "launch_bt": launch_bt,
        "dep_lifetime_sigs": len(dep_sigs),
        "dep_first_bt": dep_first_bt,
        "dep_age_at_launch_s": age,
        "dep_sniped_own": sniped,
        "snipe_txs_checked": snipe_checked,
    }

def main():
    cohorts = json.load(open(os.path.join(os.path.dirname(__file__), "cohorts.json")))
    out = json.load(open(OUT)) if os.path.exists(OUT) else {}
    todo = []
    for grp in ("winners", "losers"):
        for row in cohorts[grp]:
            todo.append((grp, row["mint"], row.get("sym")))
    # include HOODLANA as a labeled positive
    todo.append(("rug_hoodlana", "C4TFLdu1f2iGmKVv7crWVwQfRLApTgUFupxsvwvApump", "HOODLANA"))
    for grp, mint, sym in todo:
        if mint in out:
            continue
        print(f"[{grp}] {sym} {mint[:10]} ...")
        try:
            f = feature_for(mint)
        except Exception as e:
            f = {"error": str(e)[:100]}
        f["group"] = grp
        f["sym"] = sym
        out[mint] = f
        out["_rpc_stats"] = R.stats()
        tmp = OUT + ".tmp"
        json.dump(out, open(tmp, "w"), indent=1)
        os.replace(tmp, OUT)
        print("   ->", {k: f.get(k) for k in ("deployer", "dep_lifetime_sigs", "dep_age_at_launch_s", "dep_sniped_own", "reached_genesis")}, "rpc", R.stats())
    print("DONE", R.stats())

if __name__ == "__main__":
    main()
