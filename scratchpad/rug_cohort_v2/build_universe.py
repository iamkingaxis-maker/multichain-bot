"""Step 1: union all local trade caches -> distinct mints w/ first-entry ts+price.
Output: scratchpad/rug_cohort_v2/mint_universe.json
"""
import json, gzip, os, glob, sys

REPO = r"C:\Users\jcole\multichain-bot"
SP = os.path.join(REPO, "scratchpad")
OUT = os.path.join(SP, "rug_cohort_v2", "mint_universe.json")

CANDS = [
    os.path.join(SP, f) for f in (
        "_full_trades.json", "_ev_trades.json", "_tcond_trades.json",
        "_trades_fresh.json", "_trades_full.json", "_trades_full_2026_07_06.json",
        "_trades_now.json", "_trades_new.json", "_vf_trades.json", "_df_full.json.gz",
        "_ng_trades_badday_flush.json", "_ng_trades_badday_young_absorb.json",
        "_tp_trades_badday_flush.json", "_tp_trades_badday_young_absorb.json",
        "_tp_trades_badday_adolescent_absorb.json",
    )
] + glob.glob(os.path.join(REPO, "analysis", "legacy_data", "*trades*.json")) \
  + [os.path.join(REPO, "analysis", "legacy_data", "all.json"),
     os.path.join(REPO, "analysis", "legacy_data", "initial_buys.json")] \
  + glob.glob(os.path.join(REPO, "analysis", "winloss_8hr", "*trades*.json")) \
  + [os.path.join(REPO, "analysis", "_prune_mine", "_overall_trades.json"),
     os.path.join(REPO, "analysis", "_research", "trades_full.json"),
     os.path.join(REPO, "analysis", "2026-06", "data", "_crash_trades.json"),
     os.path.join(REPO, "analysis", "2026-06", "data", "_nf_trades.json")]

first = {}   # mint -> {entry_ts, entry_price, token, pair_address, src}
n_files = 0

def ingest(trades, src):
    got = 0
    for t in trades:
        if not isinstance(t, dict):
            continue
        mint = t.get("address") or t.get("mint") or t.get("token_address")
        ep = t.get("entry_price")
        if not mint or not isinstance(mint, str) or len(mint) < 30:
            continue
        ts = t.get("time") or t.get("timestamp") or t.get("entry_ts") or t.get("ts")
        try:
            ts_f = float(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts_f = None
        try:
            ep_f = float(ep) if ep is not None else None
        except (TypeError, ValueError):
            ep_f = None
        got += 1
        cur = first.get(mint)
        if cur is None or ((ts_f or 1e18) < (cur["entry_ts"] or 1e18)):
            first[mint] = {"mint": mint, "entry_ts": ts_f,
                           "entry_price": ep_f if ep_f else (cur or {}).get("entry_price"),
                           "token": t.get("token") or (cur or {}).get("token"),
                           "pair_address": t.get("pair_address") or (cur or {}).get("pair_address"),
                           "src": src}
        elif cur.get("entry_price") is None and ep_f:
            cur["entry_price"] = ep_f
        if first[mint].get("pair_address") is None and t.get("pair_address"):
            first[mint]["pair_address"] = t["pair_address"]
    return got

for p in CANDS:
    if not os.path.exists(p):
        continue
    try:
        if p.endswith(".gz"):
            with gzip.open(p, "rt", encoding="utf-8") as f:
                d = json.load(f)
        else:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
    except Exception as e:
        print(f"SKIP {os.path.basename(p)}: {type(e).__name__} {e}", file=sys.stderr)
        continue
    trades = d if isinstance(d, list) else (d.get("trades") or d.get("data") or [])
    got = ingest(trades, os.path.basename(p))
    n_files += 1
    print(f"{os.path.basename(p):40s} rows={len(trades):6d} usable={got:6d} cum_mints={len(first)}")

json.dump(list(first.values()), open(OUT, "w"), indent=0)
print(f"\nfiles={n_files} distinct mints={len(first)} -> {OUT}")
# overlap vs already-labeled
labeled = set()
lp = os.path.join(SP, "rug_cohort_labels.jsonl")
if os.path.exists(lp):
    for line in open(lp, encoding="utf-8"):
        try:
            labeled.add(json.loads(line)["mint"])
        except Exception:
            pass
new = [m for m in first if m not in labeled]
print(f"already labeled={len(labeled)}; NEW (unlabeled)={len(new)}")
with_price = sum(1 for m in new if first[m]["entry_price"])
print(f"new with entry_price={with_price}")
