"""Age-band x UTC-hour-block mine on the union positions dataset.

Four-half discipline (RH bar): chrono W1/W2 + odd/even day-of-month.
Key question: young band (<6h) in 03-08 UTC vs young band rest-of-day.
Survivorship: report per-week 03-08 fill counts first (block eras!).
"""
import json, os, statistics as st
from collections import defaultdict

ROOT = r"C:\Users\jcole\multichain-bot\scratchpad\sol_young_regime"
rows = [json.loads(l) for l in open(os.path.join(ROOT, "positions.jsonl"), encoding="utf-8")]

def band(a):
    if a is None: return None
    if a < 6: return "young"
    if a < 24: return "mid"
    return "older"

def blk(h):
    if 3 <= h < 8: return "03-08"
    if 9 <= h < 13: return "09-13"
    if 13 <= h < 22: return "13-22"
    return "other"

for r in rows:
    r["band"] = band(r["age_h"])
    r["blk"] = blk(r["utc_hour"])
    r["in0308"] = 3 <= r["utc_hour"] < 8
    r["dom"] = int(r["day"][8:10])

# ---- 1. survivorship map: per-day 03-08 fills (all bands) ----
perday = defaultdict(lambda: [0, 0])
for r in rows:
    perday[r["day"]][0] += 1
    if r["in0308"]: perday[r["day"]][1] += 1
print("== per-day total / 03-08 fills ==")
for d in sorted(perday):
    t, n = perday[d]
    print(f"{d}  {t:4d}  {n:3d}" + ("   <-- 03-08 OPEN" if n >= 5 else ""))

def cell_stats(rr):
    if not rr: return None
    pn = [r["pnl_pct"] for r in rr]
    toks = defaultdict(list)
    for r in rr: toks[r["address"]].append(r["pnl_pct"])
    tokmeds = [st.median(v) for v in toks.values()]
    return {
        "n": len(rr), "ntok": len(toks),
        "wr": sum(1 for p in pn if p > 0) / len(pn) * 100,
        "med": st.median(pn), "tokmed": st.median(tokmeds),
        "mean": st.fmean(pn),
    }

def fmt(c):
    if c is None: return "n=0"
    return (f"n={c['n']:4d} tok={c['ntok']:3d} wr={c['wr']:5.1f}% "
            f"med={c['med']:+6.2f} tokmed={c['tokmed']:+6.2f} mean={c['mean']:+6.2f}")

# ---- 2. restrict the block test to OPEN-era days (>=5 fills in 03-08) ----
open_days = {d for d, (t, n) in perday.items() if n >= 5}
sub = [r for r in rows if r["day"] in open_days and r["band"]]
days_sorted = sorted(open_days)
print(f"\nopen-era days ({len(days_sorted)}): {days_sorted[0]} -> {days_sorted[-1]}")
mid = days_sorted[len(days_sorted) // 2]
print("chrono split at day >=", mid)

halves = {
    "W1": lambda r: r["day"] < mid,
    "W2": lambda r: r["day"] >= mid,
    "even": lambda r: r["dom"] % 2 == 0,
    "odd": lambda r: r["dom"] % 2 == 1,
}

print("\n== FOUR-HALF TABLE: band x (03-08 vs rest) on open-era days ==")
verdict = {}
for b in ("young", "mid", "older"):
    print(f"\n--- band {b} ---")
    dirs = []
    for hname, hf in halves.items():
        rr = [r for r in sub if r["band"] == b and hf(r)]
        c_in = cell_stats([r for r in rr if r["in0308"]])
        c_out = cell_stats([r for r in rr if not r["in0308"]])
        d_wr = (c_in["wr"] - c_out["wr"]) if c_in and c_out else None
        d_tm = (c_in["tokmed"] - c_out["tokmed"]) if c_in and c_out else None
        print(f"{hname:4s} 03-08: {fmt(c_in)}")
        print(f"     rest : {fmt(c_out)}")
        if d_wr is not None:
            print(f"     delta: wr {d_wr:+5.1f}pp  tokmed {d_tm:+6.2f}")
            dirs.append((hname, d_wr, d_tm))
    verdict[b] = dirs
    if len(dirs) == 4:
        wr_neg = sum(1 for _, w, _ in dirs if w < 0)
        tm_neg = sum(1 for _, _, t in dirs if t < 0)
        print(f"  => 03-08 WORSE-than-rest agreement: wr {wr_neg}/4, tokmed {tm_neg}/4")

# ---- 3. full band x block table (all eras, context) ----
print("\n== band x block, ALL data (context; era-confounded) ==")
for b in ("young", "mid", "older"):
    for k in ("03-08", "09-13", "13-22", "other"):
        rr = [r for r in rows if r["band"] == b and r["blk"] == k]
        print(f"{b:6s} {k:6s} {fmt(cell_stats(rr))}")

# ---- 4. young-band hour-by-hour on open-era days ----
print("\n== young band by single hour (open-era days) ==")
for h in range(24):
    rr = [r for r in sub if r["band"] == "young" and r["utc_hour"] == h]
    if rr: print(f"h{h:02d} {fmt(cell_stats(rr))}")

# ---- 5. catastrophe rate (proxy rug at position level: pnl <= -30%) ----
print("\n== catastrophic close rate (pnl<=-30%) band x 03-08 (open era) ==")
for b in ("young", "mid", "older"):
    for lab, sel in (("03-08", True), ("rest", False)):
        rr = [r for r in sub if r["band"] == b and r["in0308"] == sel]
        if rr:
            cat = sum(1 for r in rr if r["pnl_pct"] <= -30) / len(rr) * 100
            cat10 = sum(1 for r in rr if r["pnl_pct"] <= -10) / len(rr) * 100
            print(f"{b:6s} {lab:6s} n={len(rr):4d} cat30={cat:4.1f}% cat10={cat10:4.1f}%")
