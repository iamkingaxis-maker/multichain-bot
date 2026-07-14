"""Join decision-time entry_meta features onto labeled entries."""
import json
from datetime import datetime

entries = json.load(open("scratchpad/_ng_entries.json"))
idx = {}
for bot in ["badday_flush", "badday_young_absorb"]:
    rows = json.load(open(f"scratchpad/_ng_meta_{bot}.json"))
    for r in rows:
        if r["type"] != "buy":
            continue
        ts = datetime.fromisoformat(r["time"]).timestamp()
        idx[(bot, r["address"], round(ts, 0))] = r

n_hit = n_miss = 0
for e in entries:
    k = (e["bot"], e["address"], round(e["buy_ts"], 0))
    b = idx.get(k)
    if b is None:
        # fallback: nearest buy for same token within 5s
        cands = [(abs(kk[2] - e["buy_ts"]), v) for kk, v in idx.items()
                 if kk[0] == e["bot"] and kk[1] == e["address"] and abs(kk[2] - e["buy_ts"]) <= 5]
        b = min(cands)[1] if cands else None
    if b is None:
        n_miss += 1
        e["feat"] = None
        continue
    n_hit += 1
    e["feat"] = b.get("entry_meta") or {}
    e["triggers_fired"] = b.get("triggers_fired")
    e["amount_usd"] = b.get("amount_usd")
print("joined:", n_hit, "missing buy row:", n_miss)

# feature availability
from collections import Counter
avail = Counter()
tot = 0
for e in entries:
    f = e.get("feat")
    if not f:
        continue
    tot += 1
    for k, v in f.items():
        if v is not None:
            avail[k] += 1
print("entries with feat:", tot)
for k in sorted(avail):
    if avail[k] < tot:
        print(f"  {k}: {avail[k]}/{tot}")
# None-tape overlap: unique_buyers_n None vs rt_buys_n None
nb = [e for e in entries if e.get("feat") and e["feat"].get("unique_buyers_n") is None]
print("buyers=None entries:", len(nb))
rtn = sum(1 for e in nb if e["feat"].get("rt_buys_n") is None)
print("  of those, rt_buys_n also None:", rtn)
lab = Counter((e["bot"], e["label"]) for e in nb)
print("  labels:", dict(lab))

json.dump(entries, open("scratchpad/_ng_dataset.json", "w"), indent=1)
print("wrote scratchpad/_ng_dataset.json")
