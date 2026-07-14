"""Label the 223 touched Solana mints as rugged/dead vs alive using dexscreener
(public API, NO RPC credits). Rug = deep drawdown-from-our-entry AND low current liq.
Checkpoints to rug_labels.json in batches."""
import json, os, sys, time, statistics
from collections import defaultdict
from curl_cffi import requests as cr

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "rug_labels.json")

# entry prices per mint (median of our buys)
d = json.load(open(os.path.join(HERE, "..", "_full_trades.json")))
entry = defaultdict(list)
for x in d:
    if x.get("type") == "buy" and x.get("address") and x.get("entry_price"):
        try:
            entry[x["address"]].append(float(x["entry_price"]))
        except Exception:
            pass
mints = sorted(entry.keys())
entry_med = {m: statistics.median(v) for m, v in entry.items()}

out = json.load(open(OUT)) if os.path.exists(OUT) else {}

def fetch_batch(addrs):
    url = "https://api.dexscreener.com/latest/dex/tokens/" + ",".join(addrs)
    r = cr.get(url, impersonate="chrome", timeout=20)
    return r.json().get("pairs") or []

B = 30
for i in range(0, len(mints), B):
    batch = [m for m in mints[i:i+B] if m not in out]
    if not batch:
        continue
    try:
        pairs = fetch_batch(batch)
    except Exception as e:
        print("batch err", str(e)[:80]); time.sleep(3); continue
    # index best pair per token (max liq)
    best = {}
    for p in pairs:
        base = (p.get("baseToken") or {}).get("address")
        if not base:
            continue
        liq = (p.get("liquidity") or {}).get("usd") or 0
        if base not in best or liq > (best[base].get("liquidity") or {}).get("usd", 0):
            best[base] = p
    for m in batch:
        p = best.get(m)
        if not p:
            out[m] = {"found": False, "entry_price": entry_med.get(m)}
            continue
        try:
            price_now = float(p.get("priceUsd") or 0)
        except Exception:
            price_now = 0.0
        liq_now = (p.get("liquidity") or {}).get("usd") or 0
        ep = entry_med.get(m)
        dd = ((price_now - ep) / ep * 100.0) if (ep and ep > 0) else None
        out[m] = {"found": True, "price_now": price_now, "liq_now": liq_now,
                  "fdv": p.get("fdv"), "entry_price": ep, "dd_from_entry_pct": dd,
                  "sym": (p.get("baseToken") or {}).get("symbol")}
    tmp = OUT + ".tmp"; json.dump(out, open(tmp, "w"), indent=1); os.replace(tmp, OUT)
    print(f"batch {i//B}: {len(out)} labeled")
    time.sleep(1.0)

# summarize
found = [m for m in mints if out.get(m, {}).get("found")]
rug = [m for m in found if (out[m].get("dd_from_entry_pct") is not None
       and out[m]["dd_from_entry_pct"] <= -80 and (out[m].get("liq_now") or 0) < 10000)]
dead_liq = [m for m in found if (out[m].get("liq_now") or 0) < 2000]
print("total mints", len(mints), "found on dex", len(found))
print("RUG (dd<=-80% & liq<$10k):", len(rug))
print("dead_liq (<$2k):", len(dead_liq))
json.dump(out, open(OUT, "w"), indent=1)
