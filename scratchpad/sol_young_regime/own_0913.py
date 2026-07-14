"""YOUNG (<6h) x 09-13 UTC on own closed positions (union positions.jsonl).

09-13 was never blocked -> full history usable. Four halves (chrono W1/W2 + odd/even dom).
Extras: era splits, young-LANE-bots-only slice, adjacent single hours, entry-feature composition.
"""
import json, os, statistics as st
from collections import defaultdict

ROOT = r"C:\Users\jcole\multichain-bot\scratchpad\sol_young_regime"
rows = [json.loads(l) for l in open(os.path.join(ROOT, "positions.jsonl"), encoding="utf-8")]

def band(a):
    if a is None: return None
    return "young" if a < 6 else ("mid" if a < 24 else "older")

for r in rows:
    r["band"] = band(r["age_h"])
    r["in0913"] = 9 <= r["utc_hour"] < 13
    r["dom"] = int(r["day"][8:10])

days = sorted({r["day"] for r in rows})
mid_day = days[len(days) // 2]
print(f"positions {len(rows)}  days {days[0]} -> {days[-1]} ({len(days)})  chrono split {mid_day}")

halves = {
    "W1": lambda r: r["day"] < mid_day,
    "W2": lambda r: r["day"] >= mid_day,
    "even": lambda r: r["dom"] % 2 == 0,
    "odd": lambda r: r["dom"] % 2 == 1,
}

def cell(rr):
    if not rr: return None
    pn = [r["pnl_pct"] for r in rr]
    toks = defaultdict(list)
    for r in rr: toks[r["address"]].append(r["pnl_pct"])
    tm = [st.median(v) for v in toks.values()]
    return {"n": len(rr), "ntok": len(toks),
            "wr": sum(1 for p in pn if p > 0) / len(pn) * 100,
            "med": st.median(pn), "tokmed": st.median(tm), "mean": st.fmean(pn),
            "cat30": sum(1 for p in pn if p <= -30) / len(pn) * 100,
            "cat10": sum(1 for p in pn if p <= -10) / len(pn) * 100}

def fmt(c):
    if c is None: return "n=0"
    return (f"n={c['n']:4d} tok={c['ntok']:3d} wr={c['wr']:5.1f}% med={c['med']:+6.2f} "
            f"tokmed={c['tokmed']:+6.2f} mean={c['mean']:+6.2f} cat30={c['cat30']:4.1f}% cat10={c['cat10']:4.1f}%")

def fourhalf(sub, label, inf=lambda r: r["in0913"]):
    print(f"\n== {label}: 09-13 vs rest, four halves ==")
    aw = at = 0; nh = 0
    for hn, hf in halves.items():
        rr = [r for r in sub if hf(r)]
        ci = cell([r for r in rr if inf(r)])
        co = cell([r for r in rr if not inf(r)])
        print(f"{hn:4s} 09-13: {fmt(ci)}")
        print(f"     rest : {fmt(co)}")
        if ci and co:
            dw, dt = ci["wr"] - co["wr"], ci["tokmed"] - co["tokmed"]
            dc = ci["cat30"] - co["cat30"]
            print(f"     delta: wr {dw:+5.1f}pp tokmed {dt:+6.2f} cat30 {dc:+5.1f}pp")
            aw += dw < 0; at += dt < 0; nh += 1
    print(f"  => 09-13-WORSE agreement: wr {aw}/{nh}  tokmed {at}/{nh}")

young = [r for r in rows if r["band"] == "young"]
fourhalf(young, "OWN young ALL data")

# era splits
open_era = [r for r in young if r["day"] <= "2026-06-30"]
blk_era = [r for r in young if r["day"] >= "2026-07-01"]
fourhalf_days = lambda sub: sorted({r["day"] for r in sub})
for lab, sub in (("OWN young OPEN era (<=06-30)", open_era), ("OWN young BLOCK era (>=07-01)", blk_era)):
    d = fourhalf_days(sub)
    if len(d) >= 4:
        m = d[len(d) // 2]
        hs = {"W1": lambda r, m=m: r["day"] < m, "W2": lambda r, m=m: r["day"] >= m,
              "even": lambda r: r["dom"] % 2 == 0, "odd": lambda r: r["dom"] % 2 == 1}
        print(f"\n== {lab}: 09-13 vs rest (chrono split {m}, {len(d)} days) ==")
        aw = at = nh = 0
        for hn, hf in hs.items():
            rr = [r for r in sub if hf(r)]
            ci = cell([r for r in rr if r["in0913"]]); co = cell([r for r in rr if not r["in0913"]])
            print(f"{hn:4s} 09-13: {fmt(ci)}")
            print(f"     rest : {fmt(co)}")
            if ci and co:
                dw, dt = ci["wr"] - co["wr"], ci["tokmed"] - co["tokmed"]
                print(f"     delta: wr {dw:+5.1f}pp tokmed {dt:+6.2f} cat30 {ci['cat30']-co['cat30']:+5.1f}pp")
                aw += dw < 0; at += dt < 0; nh += 1
        print(f"  => 09-13-WORSE agreement: wr {aw}/{nh}  tokmed {at}/{nh}")

# young-LANE bots only (the 3 live bots + twins)
lane = [r for r in young if "young" in (r.get("bot_id") or "")]
bots = sorted({r["bot_id"] for r in lane})
print(f"\n== young-LANE bots only ({bots}) n={len(lane)} ==")
ci = cell([r for r in lane if r["in0913"]]); co = cell([r for r in lane if not r["in0913"]])
print(f"09-13: {fmt(ci)}")
print(f"rest : {fmt(co)}")
perday = defaultdict(lambda: [0, 0])
for r in lane:
    perday[r["day"]][0] += 1
    if r["in0913"]: perday[r["day"]][1] += 1
print("lane per-day total/0913:", {d: tuple(v) for d, v in sorted(perday.items())})

# adjacent single hours 07..15, young, all data
print("\n== OWN young by single hour (all data) ==")
for h in range(24):
    rr = [r for r in young if r["utc_hour"] == h]
    if rr: print(f"h{h:02d} {fmt(cell(rr))}")

# mid/older context in 09-13
print("\n== context: mid/older 09-13 vs rest (all data) ==")
for b in ("mid", "older"):
    sub = [r for r in rows if r["band"] == b]
    ci = cell([r for r in sub if r["in0913"]]); co = cell([r for r in sub if not r["in0913"]])
    print(f"{b:6s} 09-13: {fmt(ci)}")
    print(f"{'':6s} rest : {fmt(co)}")

# compositional: entry features young 09-13 vs young rest
print("\n== composition: entry-feature medians, young 09-13 vs rest ==")
def med(vals):
    vals = [v for v in vals if v is not None]
    return st.median(vals) if vals else None
for feat in ("liq", "sol_pc_h1", "bs_h1", "age_h", "amount_usd", "hold_secs", "peak"):
    a = med([r.get(feat) for r in young if r["in0913"]])
    b = med([r.get(feat) for r in young if not r["in0913"]])
    fa = "None" if a is None else f"{a:.2f}"; fb = "None" if b is None else f"{b:.2f}"
    print(f"{feat:12s} 0913={fa:>10s}  rest={fb:>10s}")

# bot mix in young 09-13 vs rest (composition of WHO trades then)
print("\n== bot mix (top 8), young 09-13 vs rest ==")
for lab, sel in (("09-13", True), ("rest", False)):
    c = defaultdict(int)
    for r in young:
        if r["in0913"] == sel: c[r["bot_id"]] += 1
    top = sorted(c.items(), key=lambda kv: -kv[1])[:8]
    tot = sum(c.values())
    print(lab, " ".join(f"{k}:{v}({v/tot*100:.0f}%)" for k, v in top))
