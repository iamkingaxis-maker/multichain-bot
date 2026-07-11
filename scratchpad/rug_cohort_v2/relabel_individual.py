"""Step 2b: the DS 30-mint batch endpoint DROPS pairs (verified: 11/15 'pair-gone'
mints resolve individually, one at +313%). Relabel every catastrophic/dead row
from BOTH label files with one-mint-per-request queries. Alive labels are stable
(individual query can only surface MORE liquidity).
Writes scratchpad/rug_cohort_v2/labels_final.jsonl (full corrected union),
checkpointing per mint via labels_relabel_progress.jsonl.
"""
import json, os, time, sys

REPO = r"C:\Users\jcole\multichain-bot"
V2 = os.path.join(REPO, "scratchpad", "rug_cohort_v2")
PROG = os.path.join(V2, "labels_relabel_progress.jsonl")
FINAL = os.path.join(V2, "labels_final.jsonl")

rows = {}
for p in (os.path.join(REPO, "scratchpad", "rug_cohort_labels.jsonl"),
          os.path.join(V2, "labels_v2.jsonl")):
    for line in open(p, encoding="utf-8"):
        try:
            r = json.loads(line)
            rows.setdefault(r["mint"], r)  # first-writer wins (original file first)
        except Exception:
            continue

done = {}
if os.path.exists(PROG):
    for line in open(PROG, encoding="utf-8"):
        try:
            r = json.loads(line)
            done[r["mint"]] = r
        except Exception:
            continue

todo = [r for r in rows.values() if r["label"] in ("catastrophic", "dead")
        and r["mint"] not in done]
print(f"total={len(rows)} suspect(cat/dead)={sum(1 for r in rows.values() if r['label'] in ('catastrophic','dead'))} "
      f"already_requeried={len(done)} todo={len(todo)}", flush=True)

from curl_cffi import requests as cf
with open(PROG, "a", encoding="utf-8") as f:
    for i, r in enumerate(todo):
        for attempt in range(3):
            try:
                resp = cf.get(f"https://api.dexscreener.com/latest/dex/tokens/{r['mint']}",
                              impersonate="chrome", timeout=25)
                if resp.status_code == 200:
                    break
                print(f"HTTP {resp.status_code} on {r['mint'][:8]} attempt {attempt}", flush=True)
            except Exception as e:
                print(f"ERR {type(e).__name__} on {r['mint'][:8]} attempt {attempt}", flush=True)
            time.sleep(8.0)
        else:
            print("3 failures — aborting (checkpointed)", flush=True)
            sys.exit(1)
        pairs = (resp.json() or {}).get("pairs") or []
        best = None
        for p in pairs:
            liq = float(((p.get("liquidity") or {}).get("usd")) or 0)
            if best is None or liq > best[1]:
                try:
                    pr = float(p.get("priceUsd") or 0)
                except (TypeError, ValueError):
                    pr = 0.0
                best = (pr, liq)
        if best is None:
            label, price_now, liq_now, ret = "catastrophic", None, None, None
        else:
            price_now, liq_now = best
            ret = ((price_now / r["entry_price"]) - 1) * 100 if r.get("entry_price") else None
            if ret is not None and ret <= -90 and (liq_now or 0) < 5000:
                label = "catastrophic"
            elif (ret is not None and ret <= -80) or (liq_now or 0) < 5000:
                label = "dead"
            else:
                label = "alive"
        rec = dict(r)
        rec.update({"label": label, "price_now": price_now, "liq_now": liq_now,
                    "ret_pct": round(ret, 2) if ret is not None else None,
                    "relabel_ts": time.time(), "old_label": r["label"],
                    "n_pairs_individual": len(pairs)})
        f.write(json.dumps(rec) + "\n")
        f.flush()
        if (i + 1) % 20 == 0:
            print(f"{i+1}/{len(todo)}", flush=True)
        time.sleep(2.0)

# merge: corrected rows override
for line in open(PROG, encoding="utf-8"):
    try:
        r = json.loads(line)
        rows[r["mint"]] = r
    except Exception:
        continue
with open(FINAL, "w", encoding="utf-8") as f:
    for r in rows.values():
        f.write(json.dumps(r) + "\n")
from collections import Counter
print("FINAL:", Counter(r["label"] for r in rows.values()), flush=True)
flips = sum(1 for r in rows.values() if r.get("old_label") and r["old_label"] != r["label"])
print(f"flips: {flips}", flush=True)
