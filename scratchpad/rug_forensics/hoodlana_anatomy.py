"""Step 1: HOODLANA anatomy. Deployer, funder lineage, early buyers, LP custody.
Checkpoints to hoodlana_anatomy.json after EVERY sub-step so a death loses nothing."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import rpc_lib as R

MINT = "C4TFLdu1f2iGmKVv7crWVwQfRLApTgUFupxsvwvApump"
OUT = os.path.join(os.path.dirname(__file__), "hoodlana_anatomy.json")

def save(d):
    d["_rpc_stats"] = R.stats()
    d["_saved"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, OUT)
    print("  checkpoint saved. rpc:", R.stats())

def fee_payer(tx):
    try:
        return tx["transaction"]["message"]["accountKeys"][0]["pubkey"]
    except Exception:
        try:
            return tx["transaction"]["message"]["accountKeys"][0]
        except Exception:
            return None

def instructions(tx):
    try:
        return tx["transaction"]["message"]["instructions"]
    except Exception:
        return []

def main():
    d = json.load(open(OUT)) if os.path.exists(OUT) else {"mint": MINT}

    # 1) page mint sigs to creation
    if "creation" not in d:
        print("paging mint sigs to creation...")
        sigs, hit = R.oldest_sigs(MINT, max_pages=8, page=1000)
        d["mint_total_sigs_seen"] = len(sigs)
        d["mint_reached_creation"] = hit
        if sigs:
            creation_sig = sigs[-1]["signature"]
            d["creation_sig"] = creation_sig
            d["creation_blocktime"] = sigs[-1].get("blockTime")
            # first ~40 sigs chronological (oldest last in list)
            d["earliest_sigs"] = [s["signature"] for s in sigs[-40:]]
            d["earliest_blocktimes"] = [s.get("blockTime") for s in sigs[-40:]]
        save(d)

    # 2) creation tx -> deployer
    if "creation" not in d and d.get("creation_sig"):
        print("fetching creation tx...")
        tx = R.get_tx(d["creation_sig"])
        dep = fee_payer(tx) if tx else None
        progs = []
        for ins in instructions(tx or {}):
            pid = ins.get("programId") or ins.get("program")
            if pid:
                progs.append(pid)
        d["creation"] = {"deployer": dep, "programs": progs,
                         "is_pumpfun": R.PUMP_FUN in progs}
        d["deployer"] = dep
        save(d)

    # 3) deployer funding lineage: page deployer to oldest, find first inbound SOL
    dep = d.get("deployer")
    if dep and "deployer_lineage" not in d:
        print("paging deployer sigs to origin...")
        dsigs, dhit = R.oldest_sigs(dep, max_pages=6, page=1000)
        d["deployer_total_sigs_seen"] = len(dsigs)
        d["deployer_reached_origin"] = dhit
        d["deployer_first_sig"] = dsigs[-1]["signature"] if dsigs else None
        d["deployer_first_blocktime"] = dsigs[-1].get("blockTime") if dsigs else None
        save(d)
        # funder = counterparty in the earliest SOL-transfer tx into deployer
        funder = None
        funder_sig = None
        for s in reversed(dsigs[-8:]):  # earliest few
            tx = R.get_tx(s["signature"])
            if not tx:
                continue
            for ins in instructions(tx):
                if (ins.get("program") == "system" and
                        (ins.get("parsed") or {}).get("type") == "transfer"):
                    info = ins["parsed"]["info"]
                    if info.get("destination") == dep:
                        funder = info.get("source")
                        funder_sig = s["signature"]
                        break
            if funder:
                break
        d["deployer_lineage"] = {"funder": funder, "funder_sig": funder_sig}
        save(d)

    # 4) deployer prior-token count: scan deployer history for pump.fun create ixs
    if dep and "deployer_prior_tokens" not in d:
        print("scanning deployer for other token creations...")
        dsigs2, _ = R.oldest_sigs(dep, max_pages=4, page=1000)
        created_mints = set()
        checked = 0
        # sample earliest 30 txs of deployer for create instructions
        for s in reversed(dsigs2[-60:]):
            tx = R.get_tx(s["signature"])
            checked += 1
            if not tx:
                continue
            for ins in instructions(tx):
                pid = ins.get("programId") or ins.get("program")
                if pid == R.PUMP_FUN:
                    # find mint account in this tx's token balances
                    for tb in (tx.get("meta", {}).get("postTokenBalances") or []):
                        m = tb.get("mint")
                        if m:
                            created_mints.add(m)
            if checked >= 40:
                break
        d["deployer_prior_tokens"] = {"mints_touched": sorted(created_mints),
                                      "txs_checked": checked}
        save(d)

    # 5) early buyers + LP custody
    if "early_buyers" not in d:
        print("largest accounts + LP custody...")
        supply = R.token_supply(MINT)
        la = R.largest_accounts(MINT)
        holders = []
        for acc in la[:10]:
            owner = R.account_owner(acc.get("address"))
            holders.append({"token_acct": acc.get("address"),
                            "owner": owner,
                            "uiAmount": acc.get("uiAmount"),
                            "pct": (100.0 * (acc.get("uiAmount") or 0) / supply) if supply else None,
                            "is_program": owner in R.KNOWN_PROGRAMS if owner else None})
        d["supply"] = supply
        d["top_holders"] = holders
        save(d)

    # 6) early buyers from earliest mint txs
    if "early_buyers" not in d and d.get("earliest_sigs"):
        print("decoding early buyer txs...")
        buyers = []
        for s in d["earliest_sigs"][:25]:
            tx = R.get_tx(s)
            if not tx:
                continue
            fp = fee_payer(tx)
            progs = [ (i.get("programId") or i.get("program")) for i in instructions(tx)]
            is_trade = R.PUMP_FUN in progs or R.PUMP_SWAP in progs
            buyers.append({"sig": s, "signer": fp, "trade": is_trade,
                           "blockTime": tx.get("blockTime")})
        d["early_buyers"] = buyers
        save(d)

    print("DONE. final rpc stats:", R.stats())
    save(d)

if __name__ == "__main__":
    main()
