"""v2: TRUE-genesis deployer + freshness for a balanced DEAD vs ALIVE sample.
Pages mint to real genesis (cap 30 pages), archive-aware get_tx. Checkpoints per token."""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import rpc_lib as R

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "actor_features_v2.json")

def fee_payer(tx):
    try:
        k = tx["transaction"]["message"]["accountKeys"][0]
        return k["pubkey"] if isinstance(k, dict) else k
    except Exception:
        return None

def feature_for(mint):
    sigs, hit = R.oldest_sigs(mint, max_pages=30, page=1000)
    if not sigs:
        return {"error": "no_sigs"}
    genesis = sigs[-1]
    launch_bt = genesis.get("blockTime")
    tx = R.get_tx(genesis["signature"])
    dep = fee_payer(tx) if tx else None
    dep_sigs = R.sigs_for(dep, limit=1000) if dep else []
    dep_first_bt = dep_sigs[-1].get("blockTime") if dep_sigs else None
    age = (launch_bt - dep_first_bt) if (launch_bt and dep_first_bt) else None
    # dev sniped own launch: any of first 10 non-genesis txs signed by deployer
    sniped, checked = False, 0
    for s in sigs[-11:]:
        if s["signature"] == genesis["signature"]:
            continue
        t = R.get_tx(s["signature"]); checked += 1
        if t and fee_payer(t) == dep:
            sniped = True; break
        if checked >= 8:
            break
    return {"deployer": dep, "reached_genesis": hit, "mint_sigs_seen": len(sigs),
            "launch_bt": launch_bt, "dep_lifetime_sigs": len(dep_sigs),
            "dep_first_bt": dep_first_bt, "dep_age_at_launch_s": age,
            "dep_sniped_own": sniped, "snipe_txs_checked": checked,
            "genesis_tx_ok": tx is not None}

def main():
    split = json.load(open(os.path.join(HERE, "death_split.json")))
    lab = json.load(open(os.path.join(HERE, "rug_labels.json")))
    # ALIVE controls: highest current liquidity (clearly not rugs)
    alive = sorted([m for m in split["alive"]],
                   key=lambda m: -(lab.get(m, {}).get("liq_now") or 0))[:15]
    dead = split["dead"][:18]
    todo = [("dead", m) for m in dead] + [("alive", m) for m in alive]
    todo.append(("rug_hoodlana", "C4TFLdu1f2iGmKVv7crWVwQfRLApTgUFupxsvwvApump"))
    out = json.load(open(OUT)) if os.path.exists(OUT) else {}
    for grp, mint in todo:
        if mint in out:
            continue
        print(f"[{grp}] {mint[:10]} ...", flush=True)
        try:
            f = feature_for(mint)
        except Exception as e:
            f = {"error": str(e)[:120]}
        f["group"] = grp
        f["sym"] = lab.get(mint, {}).get("sym")
        out[mint] = f
        out["_rpc_stats"] = R.stats()
        tmp = OUT + ".tmp"; json.dump(out, open(tmp, "w"), indent=1); os.replace(tmp, OUT)
        print("   ->", {k: f.get(k) for k in ("dep_lifetime_sigs", "dep_age_at_launch_s",
              "dep_sniped_own", "reached_genesis", "genesis_tx_ok")}, "rpc", R.stats(), flush=True)
    print("DONE", R.stats())

if __name__ == "__main__":
    main()
