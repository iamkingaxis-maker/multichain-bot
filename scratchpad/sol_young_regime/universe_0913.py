"""Universe-recorder lens: young (<6h) x 09-13 UTC, entry-logic independent.
Four halves + adjacent cells (08-09, 13-14) + compositional (per-hour gated-pass rate / supply).
Same cache union as universe_mine.py (07-11 mine).
"""
import json, os, statistics as st
from collections import defaultdict

ROOT = r"C:\Users\jcole\multichain-bot"
FILES = [
    ".tmp_uni_all.json", ".uni_recorder.json", ".uni_remine.json",
    ".universe_full.json", "_univ_night.json", "_universe_all.json",
    "analysis/winloss_8hr/_uni_recorder.json",
    "analysis/winloss_8hr/universe_events.json",
    "analysis/2026-06/data/_research_ur2.json",
    "analysis/2026-06/data/_univ.json",
    "analysis/2026-06/data/_ur.json", "analysis/2026-06/data/_ur2.json",
    "analysis/_research/universe_recorder_live_full_20260603.json",
    "analysis/_research/fresh_launches_live_20260603.json",
    ".mining_overnight_0528/universe_fresh.json",
    "scratchpad/ripday/_urec_0704_big.json",
    "scratchpad/ripday/_urec_0704.json",
]
EXTRA = os.path.join(ROOT, "scratchpad", "sol_young_regime", "_urec_fresh.json")

ev = {}
for rel in FILES + ([EXTRA] if os.path.exists(EXTRA) else []):
    p = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
    if not os.path.exists(p): continue
    try: d = json.load(open(p, encoding="utf-8"))
    except Exception: continue
    if isinstance(d, dict): d = d.get("events", d.get("data", []))
    for e in d:
        if isinstance(e, dict) and e.get("event_id") and e.get("detected_at_iso"):
            ev.setdefault(e["event_id"], e)
print("union events:", len(ev))

rows = []
for e in ev.values():
    a = e.get("age_hours"); x = e.get("exit_pct")
    if a is None or x is None: continue
    t = e["detected_at_iso"]
    liq = e.get("liq_usd") or 0
    pc1 = e.get("pc_h1"); bs5 = e.get("bs_m5")
    rows.append({
        "t": t, "day": t[:10], "h": int(t[11:13]), "dom": int(t[8:10]),
        "band": ("young" if a < 6 else "mid" if a < 24 else "older"),
        "x": x, "peak": e.get("peak_pct"), "tok": e.get("token_address"),
        "gated": liq >= 25000 and pc1 is not None and pc1 <= -30 and (bs5 or 0) >= 1.0,
    })
rows.sort(key=lambda r: r["t"])
days = sorted({r["day"] for r in rows})
mid_day = days[len(days) // 2]
print(f"rows {len(rows)}  days {days[0]} -> {days[-1]} ({len(days)})  chrono split {mid_day}")

halves = {"W1": lambda r: r["day"] < mid_day, "W2": lambda r: r["day"] >= mid_day,
          "even": lambda r: r["dom"] % 2 == 0, "odd": lambda r: r["dom"] % 2 == 1}

def stats(rr):
    if not rr: return None
    xs = [r["x"] for r in rr]
    toks = defaultdict(list)
    for r in rr: toks[r["tok"]].append(r["x"])
    tm = [st.median(v) for v in toks.values()]
    return {"n": len(rr), "ntok": len(toks),
            "wr": sum(1 for v in xs if v > 0) / len(xs) * 100,
            "med": st.median(xs), "tokmed": st.median(tm),
            "peak10": sum(1 for r in rr if (r["peak"] or 0) >= 10) / len(rr) * 100,
            "cat": sum(1 for v in xs if v <= -30) / len(xs) * 100}

def fmt(c):
    if c is None: return "n=0"
    return (f"n={c['n']:5d} tok={c['ntok']:4d} wr={c['wr']:5.1f}% med={c['med']:+6.2f} "
            f"tokmed={c['tokmed']:+6.2f} peak10={c['peak10']:5.1f}% cat30={c['cat']:4.1f}%")

young = [r for r in rows if r["band"] == "young"]

print("\n== UNIVERSE raw young: 09-13 vs rest, four halves ==")
aw = at = ac = 0
for hn, hf in halves.items():
    rr = [r for r in young if hf(r)]
    ci = stats([r for r in rr if 9 <= r["h"] < 13])
    co = stats([r for r in rr if not (9 <= r["h"] < 13)])
    print(f"{hn:4s} 09-13: {fmt(ci)}")
    print(f"     rest : {fmt(co)}")
    if ci and co:
        dw, dt, dc = ci["wr"] - co["wr"], ci["tokmed"] - co["tokmed"], ci["cat"] - co["cat"]
        print(f"     delta: wr {dw:+5.1f}pp tokmed {dt:+6.2f} cat30 {dc:+4.1f}pp")
        aw += dw < 0; at += dt < 0; ac += dc > 0
print(f"  => 09-13-WORSE agreement: wr {aw}/4 tokmed {at}/4 cat30-higher {ac}/4")

print("\n== UNIVERSE GATED young (liq>=25k, pc_h1<=-30, bs_m5>=1): 09-13 vs rest, four halves ==")
aw = at = ac = 0
for hn, hf in halves.items():
    rr = [r for r in young if r["gated"] and hf(r)]
    ci = stats([r for r in rr if 9 <= r["h"] < 13])
    co = stats([r for r in rr if not (9 <= r["h"] < 13)])
    print(f"{hn:4s} 09-13: {fmt(ci)}")
    print(f"     rest : {fmt(co)}")
    if ci and co:
        dw, dt, dc = ci["wr"] - co["wr"], ci["tokmed"] - co["tokmed"], ci["cat"] - co["cat"]
        print(f"     delta: wr {dw:+5.1f}pp tokmed {dt:+6.2f} cat30 {dc:+4.1f}pp")
        aw += dw < 0; at += dt < 0; ac += dc > 0
print(f"  => 09-13-WORSE agreement: wr {aw}/4 tokmed {at}/4 cat30-higher {ac}/4")

# adjacent cells: single hours 07..15 (raw + gated)
print("\n== young by single hour (raw | gated), all data ==")
for h in range(24):
    rr = [r for r in young if r["h"] == h]
    gg = [r for r in rr if r["gated"]]
    if rr:
        print(f"h{h:02d} RAW  {fmt(stats(rr))}")
        print(f"     GATE {fmt(stats(gg))}")

# boundary cells 08-09 and 13-14 vs rest-excluding-09-13, four halves (raw)
for lo, hi, lab in ((8, 9, "08-09"), (13, 14, "13-14")):
    print(f"\n== boundary cell {lab} vs rest-excl-09-13, four halves (raw young) ==")
    aw = at = 0
    for hn, hf in halves.items():
        rr = [r for r in young if hf(r)]
        ci = stats([r for r in rr if lo <= r["h"] < hi])
        co = stats([r for r in rr if not (lo <= r["h"] < hi) and not (9 <= r["h"] < 13)])
        print(f"{hn:4s} {lab}: {fmt(ci)}")
        print(f"     rest : {fmt(co)}")
        if ci and co:
            dw, dt = ci["wr"] - co["wr"], ci["tokmed"] - co["tokmed"]
            print(f"     delta: wr {dw:+5.1f}pp tokmed {dt:+6.2f}")
            aw += dw < 0; at += dt < 0
    print(f"  => {lab}-WORSE agreement: wr {aw}/4 tokmed {at}/4")

# compositional: per-hour young event counts, gated-pass rate, gated supply
print("\n== composition: per-hour young events / gated-pass-rate / distinct gated tokens per day ==")
per_h = defaultdict(lambda: [0, 0, defaultdict(set)])
ndays = len(days)
for r in young:
    per_h[r["h"]][0] += 1
    if r["gated"]:
        per_h[r["h"]][1] += 1
        per_h[r["h"]][2][r["day"]].add(r["tok"])
for h in range(24):
    n, g, per = per_h[h]
    supply = [len(v) for v in per.values()]
    ms = st.median(supply) if supply else 0
    print(f"h{h:02d} events={n:5d} gated={g:4d} pass={g/n*100 if n else 0:4.1f}% "
          f"gated-tok/day med={ms} (days w/ any: {len(per)})")

# July block-era slice for 09-13 (current market)
jul = [r for r in young if r["day"] >= "2026-07-01"]
if jul:
    print("\n== JULY slice young 09-13 vs rest (raw) ==")
    ci = stats([r for r in jul if 9 <= r["h"] < 13])
    co = stats([r for r in jul if not (9 <= r["h"] < 13)])
    print(f"09-13: {fmt(ci)}")
    print(f"rest : {fmt(co)}")
