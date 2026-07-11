"""Universe-recorder mine: young-band x 03-08 UTC, entry-logic independent.

Events = recorder dip stream (each has age_hours, exit_pct = fwd-30m ret, peak_pct, won).
Dedupe by event_id across all caches. Four-half discipline.
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
    if not os.path.exists(p): print("MISSING", rel); continue
    try: d = json.load(open(p, encoding="utf-8"))
    except Exception as e: print("ERR", rel, str(e)[:60]); continue
    if isinstance(d, dict): d = d.get("events", d.get("data", []))
    n0 = len(ev)
    for e in d:
        if isinstance(e, dict) and e.get("event_id") and e.get("detected_at_iso"):
            ev.setdefault(e["event_id"], e)
    print(f"{rel:60s} +{len(ev)-n0:6d}")
print("union events:", len(ev))

rows = []
for e in ev.values():
    a = e.get("age_hours"); x = e.get("exit_pct")
    if a is None or x is None: continue
    t = e["detected_at_iso"]
    rows.append({
        "t": t, "day": t[:10], "h": int(t[11:13]), "dom": int(t[8:10]),
        "age": a, "band": ("young" if a < 6 else "mid" if a < 24 else "older"),
        "x": x, "peak": e.get("peak_pct"), "won": bool(e.get("won")),
        "tok": e.get("token_address"), "liq": e.get("liq_usd"),
        "pc_h1": e.get("pc_h1"),
    })
rows.sort(key=lambda r: r["t"])
days = sorted({r["day"] for r in rows})
print(f"rows {len(rows)}  days {days[0]} -> {days[-1]} ({len(days)})")

# coverage: events in 03-08 per day
pd = defaultdict(lambda: [0, 0])
for r in rows:
    pd[r["day"]][0] += 1
    if 3 <= r["h"] < 8: pd[r["day"]][1] += 1
print("\nday coverage (total / 03-08):")
for d in days: print(f"  {d} {pd[d][0]:5d} {pd[d][1]:4d}")

def stats(rr):
    if not rr: return None
    xs = [r["x"] for r in rr]
    toks = defaultdict(list)
    for r in rr: toks[r["tok"]].append(r["x"])
    tm = [st.median(v) for v in toks.values()]
    return {"n": len(rr), "ntok": len(toks),
            "wr": sum(1 for v in xs if v > 0) / len(xs) * 100,
            "med": st.median(xs), "tokmed": st.median(tm),
            "wr10": sum(1 for r in rr if (r["peak"] or 0) >= 10) / len(rr) * 100,
            "cat": sum(1 for v in xs if v <= -30) / len(xs) * 100}

def fmt(c):
    if c is None: return "n=0"
    return (f"n={c['n']:5d} tok={c['ntok']:4d} wr={c['wr']:5.1f}% med={c['med']:+6.2f} "
            f"tokmed={c['tokmed']:+6.2f} peak10={c['wr10']:5.1f}% cat30={c['cat']:4.1f}%")

mid_day = days[len(days) // 2]
halves = {
    "W1": lambda r: r["day"] < mid_day, "W2": lambda r: r["day"] >= mid_day,
    "even": lambda r: r["dom"] % 2 == 0, "odd": lambda r: r["dom"] % 2 == 1,
}
print(f"\nchrono split at {mid_day}")
print("\n== UNIVERSE four-half: band x (03-08 vs rest) ==")
for b in ("young", "mid", "older"):
    print(f"\n--- {b} ---")
    agree_wr = agree_tm = 0
    for hn, hf in halves.items():
        rr = [r for r in rows if r["band"] == b and hf(r)]
        ci = stats([r for r in rr if 3 <= r["h"] < 8])
        co = stats([r for r in rr if not (3 <= r["h"] < 8)])
        print(f"{hn:4s} 03-08: {fmt(ci)}")
        print(f"     rest : {fmt(co)}")
        if ci and co:
            dw = ci["wr"] - co["wr"]; dt = ci["tokmed"] - co["tokmed"]
            dc = ci["cat"] - co["cat"]
            print(f"     delta: wr {dw:+5.1f}pp tokmed {dt:+6.2f} cat30 {dc:+4.1f}pp")
            agree_wr += dw < 0; agree_tm += dt < 0
    print(f"  => 03-08-worse agreement: wr {agree_wr}/4  tokmed {agree_tm}/4")

# July-slice spotlight (current market, block era)
jul = [r for r in rows if r["day"] >= "2026-07-01"]
if jul:
    print("\n== JULY slice (block era, market-wide) ==")
    for b in ("young", "mid", "older"):
        rr = [r for r in jul if r["band"] == b]
        ci = stats([r for r in rr if 3 <= r["h"] < 8])
        co = stats([r for r in rr if not (3 <= r["h"] < 8)])
        print(f"{b:6s} 03-08: {fmt(ci)}")
        print(f"{'':6s} rest : {fmt(co)}")

# young by hour (all)
print("\n== young by hour, all data ==")
for h in range(24):
    rr = [r for r in rows if r["band"] == "young" and r["h"] == h]
    if rr: print(f"h{h:02d} {fmt(stats(rr))}")
