"""Batch-resolve mints via DexScreener tokens/v1 (30/call). Checkpointed."""
import json, time, os
from curl_cffi import requests as cr

mints = json.load(open("scratchpad/_toptrader_mints.json"))
OUT = "scratchpad/_toptrader_tokens.json"
tok = json.load(open(OUT)) if os.path.exists(OUT) else {}
todo = [m for m in mints if m not in tok]
print(f"{len(mints)} mints, {len(todo)} to resolve")

sess = cr.Session(impersonate="chrome", timeout=20)
DEADLINE = time.time() + 500
for i in range(0, len(todo), 30):
    if time.time() > DEADLINE:
        print("DEADLINE — rerun to resume"); break
    batch = todo[i:i+30]
    url = "https://api.dexscreener.com/tokens/v1/solana/" + ",".join(batch)
    try:
        r = sess.get(url)
        pairs = r.json() if r.status_code == 200 else []
    except Exception as e:
        print("EXC", e); time.sleep(5); continue
    best = {}
    for p in pairs or []:
        m = (p.get("baseToken") or {}).get("address")
        if not m:
            continue
        liq = ((p.get("liquidity") or {}).get("usd")) or 0
        if m not in best or liq > best[m]["liq"]:
            best[m] = {"liq": liq, "mcap": p.get("marketCap") or p.get("fdv"),
                       "created": p.get("pairCreatedAt"),
                       "dex": p.get("dexId"), "vol24": (p.get("volume") or {}).get("h24")}
    for m in batch:
        tok[m] = best.get(m)  # None if dexscreener lost it (dead/rugged)
    json.dump(tok, open(OUT, "w"))
    if (i // 30) % 10 == 0:
        print(f"  {i+len(batch)}/{len(todo)}")
    time.sleep(1.3)
resolved = sum(1 for v in tok.values() if v)
print(f"resolved {resolved}/{len(tok)} (unresolved = delisted/dead on DS)")
