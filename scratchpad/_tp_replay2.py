"""TP-peel replay v2.

HYBRID (headline): keeps every actual exit up to and including TP1 at its
ACTUAL fill pnl; only the post-TP1 remainder differs:
   realized: 0.75 @ actual TP1 fill + 0.25 @ actual subsequent legs
   PEELgb:   0.50 @ actual TP1 fill + 0.50 @ trail-sim(gb) on bars from TP1 ts
Positions with no TP1 fire are IDENTICAL under peel (pre-TP1 exits unchanged).

PURE replay (secondary): full bar sim from first full bar after entry;
gap-aware TP fills (fill = max(trigger, bar open)); downside before upside in
each bar; running peak updates end-of-bar (trail rises late = conservative);
trail/stop fills at min(trigger, bar open).
"""
import json
from datetime import datetime, timezone

pos = json.load(open("scratchpad/_tp_positions.json"))
bars_by_pid = {}
for line in open("scratchpad/_tp_bars.jsonl"):
    d = json.loads(line)
    if d.get("bars"):
        bars_by_pid[d["pid"]] = d["bars"]

def pct(v, q):
    v = sorted(v)
    return v[int(q / 100 * (len(v) - 1))] if v else float("nan")

# ---------------- pure replay ----------------
def pure(bars, ep, entry_ts, policy, gb=8.0):
    rem, realized, peak, tp1 = 1.0, 0.0, 0.0, False
    timeout, last_c = False, 0.0
    P = lambda x: (x / ep - 1.0) * 100.0
    started = False
    for ts, o, h, l, c, vol in bars:
        if ts <= entry_ts:      # skip entry bar + earlier (pre-entry flush low)
            continue
        started = True
        o, h, l, c = P(o), P(h), P(l), P(c)
        last_c = c
        if rem > 0:
            if policy == "CURRENT" or not tp1:
                if l <= -12.0:
                    realized += rem * min(-12.0, o); rem = 0.0
                elif peak >= 3.0 and l <= 0.0:
                    realized += rem * min(0.0, o); rem = 0.0
            else:
                trail = max(peak - gb, -12.0)
                if l <= trail:
                    realized += rem * min(trail, o); rem = 0.0
        if rem > 0:
            if policy == "CURRENT":
                if not tp1 and h >= 6.0:
                    realized += 0.75 * max(6.0, min(o, h)); rem -= 0.75; tp1 = True
                if tp1 and rem > 1e-9 and h >= 12.0:
                    realized += rem * max(12.0, min(o, h)); rem = 0.0
            else:
                if not tp1 and h >= 6.0:
                    fill = max(6.0, min(o, h))
                    realized += 0.5 * fill; rem = 0.5; tp1 = True
                    trail = max(h - gb, -12.0)   # same-bar worst case high->low
                    if l <= trail:
                        realized += rem * trail; rem = 0.0
        peak = max(peak, h)
        if rem <= 1e-9:
            break
    if rem > 1e-9:
        realized += rem * last_c
        timeout = started
    return realized, timeout

# ---------------- hybrid runner sim ----------------
def runner(bars, ep, start_ts, peak0, gb):
    """Trail sim for the runner from start_ts. Returns (fill_pnl, timeout)."""
    P = lambda x: (x / ep - 1.0) * 100.0
    peak, last_c, seen = peak0, None, False
    for ts, o, h, l, c, vol in bars:
        if ts <= start_ts:
            continue
        seen = True
        o, h, l, c = P(o), P(h), P(l), P(c)
        last_c = c
        trail = max(peak - gb, -12.0)
        if l <= trail:
            return min(trail, o), False
        peak = max(peak, h)
    if last_c is None:
        return None, True   # no bars after TP1 (position closed near bar end)
    return last_c, True

rows = []
skipped_scrub_tp1 = 0
for p in pos:
    pid = f"{p['bot']}|{p['addr']}|{p['entry_time']}"
    bars = bars_by_pid.get(pid)
    entry_ts = datetime.fromisoformat(p["entry_time"]).timestamp()
    ep = p["entry_price"]
    tp1s = [s for s in p["sells"] if s["reason"].startswith("TP1")]
    row = {"pid": pid, "bot": p["bot"], "token": p["token"], "winner": p["winner"],
           "entry_time": p["entry_time"], "peak_rec": p["peak"],
           "realized": p["realized_pp"], "has_bars": bool(bars), "tp1": bool(tp1s)}
    # ---- hybrid ----
    if not tp1s:
        row["hyb8"] = row["hyb5"] = p["realized_pp"]   # identical by construction
        row["hyb_sim"] = False
    elif not bars:
        row["hyb8"] = row["hyb5"] = None
        row["hyb_sim"] = None   # TP1 fired but no bars -> coverage gap
    else:
        s1 = tp1s[0]
        fill1 = s1["pnl"]
        t1 = datetime.fromisoformat(s1["time"]).timestamp()
        peak0 = max(s1["peak"] or 0.0, fill1)
        row["hyb_sim"] = True
        row["tp1_fill"] = fill1
        for gb, key in [(8.0, "hyb8"), (5.0, "hyb5")]:
            rf, to = runner(bars, ep, t1, peak0, gb)
            if rf is None:
                # no bars after TP1: assume runner exits at TP1 fill (neutral)
                rf, to = fill1, False
            row[key] = 0.5 * fill1 + 0.5 * rf
            row[key + "_to"] = to
            row[key + "_runner"] = rf
    # ---- pure ----
    if bars:
        row["cur"], _ = pure(bars, ep, entry_ts, "CURRENT")
        row["p8"], row["p8_to"] = pure(bars, ep, entry_ts, "PEEL", 8.0)
        row["p5"], _ = pure(bars, ep, entry_ts, "PEEL", 5.0)
    rows.append(row)

json.dump(rows, open("scratchpad/_tp_replay2_rows.json", "w"), indent=1)

BOTS = ["badday_flush", "badday_young_absorb", "badday_adolescent_absorb"]

def hyb_block(tag, rs):
    n = len(rs)
    if not n:
        print(f"  {tag}: n=0"); return
    miss = [r for r in rs if r["hyb8"] is None]
    rs = [r for r in rs if r["hyb8"] is not None]
    for k in ["realized", "hyb8", "hyb5"]:
        v = [r[k] for r in rs]
        tot = sum(v)
        top5 = sum(sorted(v, reverse=True)[:5])
        print(f"  {k:>9}: total={tot:+8.1f}pp mean={tot/len(v):+6.2f} med={pct(v,50):+6.2f} "
              f"p75={pct(v,75):+6.2f} top5={top5:+7.1f}")
    if miss:
        print(f"  (excluded {len(miss)} TP1-fired positions with no bars)")

print("=========== HYBRID (actual fills + runner sim) ===========")
for bot in BOTS:
    for lbl, sel in [("WINNERS", True), ("LOSERS", False)]:
        rs = [r for r in rows if r["bot"] == bot and r["winner"] == sel]
        nsim = sum(1 for r in rs if r.get("hyb_sim"))
        print(f"\n-- {bot} {lbl} n={len(rs)} (runner-simmed={nsim}, rest identical)")
        hyb_block(bot, rs)

print("\n-- HALVES (all bots, all positions)")
for lbl, lo, hi in [("07-01/02", "2026-07-01", "2026-07-03"),
                    ("07-03+", "2026-07-03", "2026-07-99")]:
    rs = [r for r in rows if lo <= r["entry_time"] < hi]
    print(f"\n{lbl} n={len(rs)}")
    hyb_block(lbl, rs)

print("\n-- TOTAL BOOK (hybrid)")
hyb_block("book", rows)

print("\n=========== PURE replay (secondary, all-sim) ===========")
for bot in BOTS:
    rs = [r for r in rows if r["bot"] == bot and "cur" in r]
    w = [r for r in rs if r["winner"]]; l = [r for r in rs if not r["winner"]]
    for lbl, g in [("W", w), ("L", l)]:
        if not g: continue
        print(f"{bot} {lbl} n={len(g)}: realized={sum(r['realized'] for r in g):+7.1f} "
              f"cur={sum(r['cur'] for r in g):+7.1f} p8={sum(r['p8'] for r in g):+7.1f} "
              f"p5={sum(r['p5'] for r in g):+7.1f}")

# TP1-fired positions: runner outcome detail, top movers
sim = [r for r in rows if r.get("hyb_sim")]
print(f"\nTP1-fired positions with runner sim: n={len(sim)}; "
      f"runner timeout(6h horizon end): {sum(1 for r in sim if r.get('hyb8_to'))}")
d8 = [r["hyb8"] - r["realized"] for r in sim]
d5 = [r["hyb5"] - r["realized"] for r in sim]
print(f"delta per TP1-position hyb8-realized: total={sum(d8):+.1f} med={pct(d8,50):+.2f} "
      f"p25={pct(d8,25):+.2f} p75={pct(d8,75):+.2f} win%={sum(1 for x in d8 if x>0)/len(d8):.0%}")
print(f"delta per TP1-position hyb5-realized: total={sum(d5):+.1f} med={pct(d5,50):+.2f} "
      f"p25={pct(d5,25):+.2f} p75={pct(d5,75):+.2f} win%={sum(1 for x in d5 if x>0)/len(d5):.0%}")
print("\nbiggest |delta| (hyb8) positions:")
for r in sorted(sim, key=lambda r: -abs(r["hyb8"] - r["realized"]))[:12]:
    tok = (r["token"] or "?").encode("ascii", "replace").decode()[:10]
    print(f"  {r['bot'][7:]:>16} {tok:<10} tp1_fill={r.get('tp1_fill',0):+6.1f} "
          f"realized={r['realized']:+7.1f} hyb8={r['hyb8']:+7.1f} runner8={r.get('hyb8_runner',0):+7.1f} "
          f"hyb5={r['hyb5']:+7.1f} to={r.get('hyb8_to')}")
