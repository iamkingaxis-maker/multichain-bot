"""Emulate the live young-lane entry gate on universe events:
liq>=25k AND pc_h1<=-30 AND bs_m5>=1 (demand-met proxy for nf15s>=0/buyers>=10).
Question: do GATED young events in 03-08 hold up (rulebook: demand-met overnight dips bounce fine)?
Also: candidate supply in 03-08 (throughput estimate for the 3 live young bots).
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
    "scratchpad/ripday/_urec_0704_big.json", "scratchpad/ripday/_urec_0704.json",
]
ev = {}
for rel in FILES:
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p): continue
    try: d = json.load(open(p, encoding="utf-8"))
    except Exception: continue
    if isinstance(d, dict): d = d.get("events", [])
    for e in d:
        if isinstance(e, dict) and e.get("event_id"):
            ev.setdefault(e["event_id"], e)

rows = []
for e in ev.values():
    a, x = e.get("age_hours"), e.get("exit_pct")
    if a is None or x is None or a >= 6: continue
    liq = e.get("liq_usd") or 0
    pc1 = e.get("pc_h1")
    bs5 = e.get("bs_m5")
    gated = liq >= 25000 and pc1 is not None and pc1 <= -30 and (bs5 or 0) >= 1.0
    t = e["detected_at_iso"]
    rows.append({"t": t, "day": t[:10], "h": int(t[11:13]), "dom": int(t[8:10]),
                 "x": x, "tok": e.get("token_address"), "gated": gated,
                 "cat": x <= -30})
days = sorted({r["day"] for r in rows})
mid_day = days[len(days) // 2]
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
            "cat": sum(1 for r in rr if r["cat"]) / len(rr) * 100}

def fmt(c):
    if c is None: return "n=0"
    return (f"n={c['n']:4d} tok={c['ntok']:3d} wr={c['wr']:5.1f}% med={c['med']:+6.2f} "
            f"tokmed={c['tokmed']:+6.2f} cat30={c['cat']:4.1f}%")

print(f"young events {len(rows)}, gated {sum(1 for r in rows if r['gated'])} "
      f"(chrono split {mid_day})")
print("\n== GATED young (lane-gate proxy): 03-08 vs rest, four halves ==")
aw = at = 0
for hn, hf in halves.items():
    rr = [r for r in rows if r["gated"] and hf(r)]
    ci = stats([r for r in rr if 3 <= r["h"] < 8])
    co = stats([r for r in rr if not (3 <= r["h"] < 8)])
    print(f"{hn:4s} 03-08: {fmt(ci)}")
    print(f"     rest : {fmt(co)}")
    if ci and co:
        dw, dt = ci["wr"] - co["wr"], ci["tokmed"] - co["tokmed"]
        print(f"     delta: wr {dw:+5.1f}pp tokmed {dt:+6.2f} cat30 {ci['cat']-co['cat']:+5.1f}pp")
        aw += dw < 0; at += dt < 0
print(f"  => 03-08-worse agreement: wr {aw}/4 tokmed {at}/4")

# supply: gated young events per day inside 03-08 (distinct tokens)
print("\n== candidate supply: gated young distinct tokens per day in 03-08 ==")
per = defaultdict(set)
for r in rows:
    if r["gated"] and 3 <= r["h"] < 8: per[r["day"]].add(r["tok"])
vals = [len(v) for v in per.values()]
if vals:
    print(f"days with any: {len(per)}/{len(days)}; median {st.median(vals)}, mean {st.fmean(vals):.1f}, max {max(vals)}")
