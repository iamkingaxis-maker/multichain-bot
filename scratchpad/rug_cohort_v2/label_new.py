"""Step 2: label NEW mints (not in rug_cohort_labels.jsonl) via DexScreener batch.
Same label logic as scripts/rug_cohort_label.py (curl_cffi chrome, fail-closed).
Appends to scratchpad/rug_cohort_v2/labels_v2.jsonl immediately per batch (checkpoint).
Also merges today's API pull mints into the universe.
"""
import json, os, time, sys

REPO = r"C:\Users\jcole\multichain-bot"
V2 = os.path.join(REPO, "scratchpad", "rug_cohort_v2")
OUT = os.path.join(V2, "labels_v2.jsonl")
DS_BATCH = "https://api.dexscreener.com/latest/dex/tokens/{}"

uni = {u["mint"]: u for u in json.load(open(os.path.join(V2, "mint_universe.json")))}

# merge today's pull (fresh mints + fresher entry data)
today = json.load(open(os.path.join(V2, "_trades_today.json"), encoding="utf-8"))
today = today if isinstance(today, list) else today.get("trades", [])
for t in today:
    m = t.get("address")
    if not m:
        continue
    ts = t.get("time")
    # ISO string -> epoch
    ts_f = None
    if isinstance(ts, str):
        try:
            import datetime
            ts_f = datetime.datetime.fromisoformat(ts).timestamp()
        except Exception:
            pass
    elif ts is not None:
        try:
            ts_f = float(ts)
        except Exception:
            pass
    ep = t.get("entry_price")
    cur = uni.get(m)
    if cur is None:
        uni[m] = {"mint": m, "entry_ts": ts_f, "entry_price": ep,
                  "token": t.get("token"), "pair_address": t.get("pair_address"),
                  "src": "_trades_today"}
    else:
        if ts_f and (cur.get("entry_ts") is None or ts_f < cur["entry_ts"]):
            cur["entry_ts"] = ts_f
            if ep:
                cur["entry_price"] = ep
        if cur.get("pair_address") is None and t.get("pair_address"):
            cur["pair_address"] = t["pair_address"]
json.dump(list(uni.values()), open(os.path.join(V2, "mint_universe.json"), "w"), indent=0)

labeled = set()
for p in (os.path.join(REPO, "scratchpad", "rug_cohort_labels.jsonl"), OUT):
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                labeled.add(json.loads(line)["mint"])
            except Exception:
                pass

new = [v for m, v in uni.items() if m not in labeled and v.get("entry_price")]
print(f"universe={len(uni)} already_labeled={len(labeled)} to_label={len(new)}")

from curl_cffi import requests as cf
now = time.time()
n_done = 0
with open(OUT, "a", encoding="utf-8") as f:
    for i in range(0, len(new), 30):
        chunk = new[i:i + 30]
        mints = [v["mint"] for v in chunk]
        r = cf.get(DS_BATCH.format(",".join(mints)), impersonate="chrome", timeout=25)
        if r.status_code != 200:
            print(f"HTTP {r.status_code} at batch {i//30} — stopping (checkpointed {n_done})")
            sys.exit(1)
        pairs = (r.json() or {}).get("pairs") or []
        best = {}
        for p in pairs:
            m = ((p.get("baseToken") or {}).get("address") or "")
            liq = float(((p.get("liquidity") or {}).get("usd")) or 0)
            if m and liq >= best.get(m, (None, -1))[1]:
                try:
                    price = float(p.get("priceUsd") or 0)
                except (TypeError, ValueError):
                    price = 0.0
                best[m] = (price, liq)
        for v in chunk:
            st = best.get(v["mint"])
            if st is None:
                label, price_now, liq_now, ret = "catastrophic", None, None, None
            else:
                price_now, liq_now = st
                ret = ((price_now / v["entry_price"]) - 1) * 100 if v["entry_price"] else None
                if ret is not None and ret <= -90 and (liq_now or 0) < 5000:
                    label = "catastrophic"
                elif (ret is not None and ret <= -80) or (liq_now or 0) < 5000:
                    label = "dead"
                else:
                    label = "alive"
            f.write(json.dumps({
                "mint": v["mint"], "token": v.get("token"), "label": label,
                "entry_price": v["entry_price"], "entry_ts": v.get("entry_ts"),
                "price_now": price_now, "liq_now": liq_now,
                "ret_pct": round(ret, 2) if ret is not None else None,
                "labeled_ts": now, "src": v.get("src"),
                "pair_address": v.get("pair_address"),
            }) + "\n")
            n_done += 1
        f.flush()
        print(f"batch {i//30 + 1}: {n_done}/{len(new)}")
        time.sleep(2.0)
print("done")
