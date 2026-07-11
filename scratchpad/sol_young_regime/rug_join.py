"""Rug-rate by age band x hour: join rug_cohort_v2 labels -> union positions for age."""
import json, os, statistics as st
from datetime import datetime, timezone
from collections import defaultdict

ROOT = r"C:\Users\jcole\multichain-bot"
D = os.path.join(ROOT, "scratchpad", "sol_young_regime")

labels = [json.loads(l) for l in open(
    os.path.join(ROOT, "scratchpad", "rug_cohort_v2", "labels_final.jsonl"), encoding="utf-8")]
pos = [json.loads(l) for l in open(os.path.join(D, "positions.jsonl"), encoding="utf-8")]

# index positions by address -> list of (epoch, age_h)
idx = defaultdict(list)
for p in pos:
    if p.get("age_h") is None: continue
    ts = datetime.fromisoformat(p["entry_time"]).timestamp()
    idx[p["address"]].append((ts, p["age_h"]))

joined = []
for L in labels:
    ts = L.get("entry_ts")
    if not ts: continue
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    cand = idx.get(L["mint"], [])
    age = None
    if cand:
        best = min(cand, key=lambda c: abs(c[0] - ts))
        if abs(best[0] - ts) <= 3600 * 6:
            # age at label entry = cached age + elapsed
            age = best[1] + (ts - best[0]) / 3600.0
    joined.append({
        "label": L["label"], "h": dt.hour, "day": dt.strftime("%Y-%m-%d"),
        "dom": dt.day, "age": age,
        "band": (None if age is None else "young" if age < 6 else "mid" if age < 24 else "older"),
        "in0308": 3 <= dt.hour < 8,
    })
n_age = sum(1 for j in joined if j["age"] is not None)
print(f"labels {len(joined)}, age-joined {n_age}")

def cat_rate(rr):
    if not rr: return None
    return sum(1 for r in rr if r["label"] == "catastrophic") / len(rr) * 100

print("\n== catastrophic-label rate: band x (03-08 vs rest) ==")
for b in ("young", "mid", "older", None):
    for lab, sel in (("03-08", True), ("rest", False)):
        rr = [j for j in joined if j["band"] == b and j["in0308"] == sel]
        c = cat_rate(rr)
        bn = b or "no-age"
        print(f"{bn:6s} {lab:6s} n={len(rr):4d} cat={c if c is None else round(c,1)}%")

# hour histogram of catastrophic rate (all bands, incl no-age)
print("\n== catastrophic rate by hour (all labels) ==")
for h in range(24):
    rr = [j for j in joined if j["h"] == h]
    if rr:
        print(f"h{h:02d} n={len(rr):3d} cat={cat_rate(rr):5.1f}%")

# four halves on young if possible
days = sorted({j["day"] for j in joined})
mid_day = days[len(days) // 2]
halves = {"W1": lambda r: r["day"] < mid_day, "W2": lambda r: r["day"] >= mid_day,
          "even": lambda r: r["dom"] % 2 == 0, "odd": lambda r: r["dom"] % 2 == 1}
print(f"\n== young-band cat-rate four halves (chrono split {mid_day}) ==")
for hn, hf in halves.items():
    rr = [j for j in joined if j["band"] == "young" and hf(j)]
    ci = [r for r in rr if r["in0308"]]; co = [r for r in rr if not r["in0308"]]
    print(f"{hn:4s} 03-08 n={len(ci):3d} cat={cat_rate(ci)}  rest n={len(co):3d} cat={cat_rate(co)}")
