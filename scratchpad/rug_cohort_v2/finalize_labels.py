"""Step 2c: finalize labels with the timestamp-bug fix (adversarial review 07-11).
- Reparse ALL caches with ISO-8601 support -> true first-buy entry_ts + entry_price.
- Merge: original rug_cohort_labels.jsonl + labels_v2.jsonl, overridden by
  labels_relabel_progress.jsonl (individual DS requeries; batch endpoint drops pairs).
- Recompute ret_pct from corrected entry_price; re-derive label from stored
  price_now/liq_now.
- provisional = entry_ts unknown OR (label observed < 24h after first buy).
Writes labels_final.jsonl + updates mint_universe.json entry_ts/entry_price.
"""
import json, gzip, os, glob
from datetime import datetime

REPO = r"C:\Users\jcole\multichain-bot"
SP = os.path.join(REPO, "scratchpad")
V2 = os.path.join(SP, "rug_cohort_v2")

def ts_float(ts):
    if ts is None:
        return None
    try:
        return float(ts)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None

CANDS = [os.path.join(SP, f) for f in (
    "_full_trades.json", "_ev_trades.json", "_tcond_trades.json",
    "_trades_fresh.json", "_trades_full.json", "_trades_full_2026_07_06.json",
    "_trades_now.json", "_trades_new.json", "_vf_trades.json",
    "_ng_trades_badday_flush.json", "_ng_trades_badday_young_absorb.json",
    "_tp_trades_badday_flush.json", "_tp_trades_badday_young_absorb.json",
    "_tp_trades_badday_adolescent_absorb.json",
)] + [os.path.join(V2, "_trades_today.json")] \
  + glob.glob(os.path.join(REPO, "analysis", "legacy_data", "*trades*.json")) \
  + [os.path.join(REPO, "analysis", "legacy_data", "all.json")] \
  + glob.glob(os.path.join(REPO, "analysis", "winloss_8hr", "*trades*.json")) \
  + [os.path.join(REPO, "analysis", "_prune_mine", "_overall_trades.json"),
     os.path.join(REPO, "analysis", "_research", "trades_full.json"),
     os.path.join(REPO, "analysis", "2026-06", "data", "_crash_trades.json"),
     os.path.join(REPO, "analysis", "2026-06", "data", "_nf_trades.json")]

first = {}  # mint -> (entry_ts, entry_price, token, pair_address)
for p in CANDS:
    if not os.path.exists(p):
        continue
    try:
        with (gzip.open(p, "rt", encoding="utf-8") if p.endswith(".gz")
              else open(p, encoding="utf-8")) as f:
            d = json.load(f)
    except Exception:
        continue
    for t in (d if isinstance(d, list) else (d.get("trades") or [])):
        if not isinstance(t, dict):
            continue
        m = t.get("address")
        if not m or not isinstance(m, str) or len(m) < 30:
            continue
        ts_f = ts_float(t.get("time") or t.get("timestamp"))
        try:
            ep = float(t.get("entry_price")) if t.get("entry_price") else None
        except (TypeError, ValueError):
            ep = None
        cur = first.get(m)
        if cur is not None and ts_f is None:
            continue  # never hijack the anchor with unknown ts
        if cur is None or (ts_f or 1e18) < (cur["entry_ts"] or 1e18):
            first[m] = {"entry_ts": ts_f, "entry_price": ep or (cur or {}).get("entry_price"),
                        "token": t.get("token") or (cur or {}).get("token"),
                        "pair_address": t.get("pair_address") or (cur or {}).get("pair_address")}
        elif cur.get("entry_price") is None and ep:
            cur["entry_price"] = ep

n_ts = sum(1 for v in first.values() if v["entry_ts"] is not None)
print(f"first-buy anchors: {len(first)} mints, {n_ts} with entry_ts")

# update universe
uni = {u["mint"]: u for u in json.load(open(os.path.join(V2, "mint_universe.json")))}
for m, v in first.items():
    u = uni.setdefault(m, {"mint": m, "src": "finalize"})
    u["entry_ts"] = v["entry_ts"]
    if v["entry_price"]:
        u["entry_price"] = v["entry_price"]
    if v.get("pair_address"):
        u.setdefault("pair_address", v["pair_address"])
    if v.get("token"):
        u.setdefault("token", v["token"])
json.dump(list(uni.values()), open(os.path.join(V2, "mint_universe.json"), "w"), indent=0)

# merge labels
rows = {}
for p in (os.path.join(SP, "rug_cohort_labels.jsonl"), os.path.join(V2, "labels_v2.jsonl")):
    for line in open(p, encoding="utf-8"):
        try:
            r = json.loads(line)
            rows.setdefault(r["mint"], r)
        except Exception:
            continue
for line in open(os.path.join(V2, "labels_relabel_progress.jsonl"), encoding="utf-8"):
    try:
        r = json.loads(line)
        rows[r["mint"]] = r
    except Exception:
        continue

n_flip_price = n_flip_label = 0
from collections import Counter
for m, r in rows.items():
    fb = first.get(m)
    if fb:
        if fb["entry_price"] and r.get("entry_price") and \
           abs(fb["entry_price"] - r["entry_price"]) / r["entry_price"] > 1e-9:
            n_flip_price += 1
        if fb["entry_price"]:
            r["entry_price"] = fb["entry_price"]
        r["entry_ts"] = fb["entry_ts"]
    price_now, liq_now = r.get("price_now"), r.get("liq_now")
    if price_now is not None and r.get("entry_price"):
        r["ret_pct"] = round((price_now / r["entry_price"] - 1) * 100, 2)
    old = r["label"]
    if price_now is None:
        new = "catastrophic"
    else:
        ret = r.get("ret_pct")
        if ret is not None and ret <= -90 and (liq_now or 0) < 5000:
            new = "catastrophic"
        elif (ret is not None and ret <= -80) or (liq_now or 0) < 5000:
            new = "dead"
        else:
            new = "alive"
    if new != old:
        n_flip_label += 1
    r["label"] = new
    obs = r.get("relabel_ts") or r.get("labeled_ts")
    r["provisional"] = (r["entry_ts"] is None
                        or obs is None
                        or (obs - r["entry_ts"]) < 24 * 3600)

with open(os.path.join(V2, "labels_final.jsonl"), "w", encoding="utf-8") as f:
    for r in rows.values():
        f.write(json.dumps(r) + "\n")
print(f"labels_final: {Counter(r['label'] for r in rows.values())}")
print(f"provisional (<24h or unknown ts): {sum(1 for r in rows.values() if r['provisional'])}")
print(f"mature: {Counter(r['label'] for r in rows.values() if not r['provisional'])}")
print(f"entry_price corrections: {n_flip_price}, label flips vs stored: {n_flip_label}")
