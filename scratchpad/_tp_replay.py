"""TP-peel exit replay on GT minute bars.

Policies (all fills conservative: trigger level, or bar-open if it gapped past;
within a bar, downside exits are processed BEFORE upside triggers; running peak
updates only at end-of-bar => trail rises late = worse fills):

  CURRENT     TP1 +6 sell 0.75; TP2 +12 sell rest; stop -12; BE-lock proxy
              (after peak>=3, exit rest at 0 if touched).
  PEEL8       sell 0.5 at first +6 touch; remainder trails 8pp giveback from
              running peak, floor -12, no cap. Pre-TP1 behavior identical to
              CURRENT (stop -12 + BE-lock).
  PEEL5       same, 5pp giveback.

Horizon = bars available (target 6h); leftover exits at last close (flagged).
"""
import json
from datetime import datetime, timezone
from collections import defaultdict

pos = {f"{p['bot']}|{p['addr']}|{p['entry_time']}": p
       for p in json.load(open("scratchpad/_tp_positions.json"))}

bars_by_pid = {}
for line in open("scratchpad/_tp_bars.jsonl"):
    d = json.loads(line)
    if d.get("bars"):
        bars_by_pid[d["pid"]] = d["bars"]  # [[ts,o,h,l,c,vol],...] ascending


def replay(bars, entry_price, entry_ts, policy, gb=8.0):
    """Return dict(net_pp, legs, timeout, peak_seen)."""
    # pnl-space bars from entry bar onward; entry bar: only the part >= entry is
    # unknowable — conservative: treat entry bar with full range (its low may
    # predate entry => can only hurt us via earlier stop = conservative).
    rem, realized, peak = 1.0, 0.0, 0.0
    tp1 = False
    legs = []
    timeout = False
    pnl = lambda price: (price / entry_price - 1.0) * 100.0
    fired = False
    last_c = 0.0
    for ts, o, h, l, c, vol in bars:
        if ts < entry_ts - 60:
            continue
        o, h, l, c = pnl(o), pnl(h), pnl(l), pnl(c)
        last_c = c
        # ---- downside first (conservative) ----
        if rem > 0:
            if policy == "CURRENT" or not tp1:
                # hard stop
                if l <= -12.0:
                    fill = min(-12.0, o)
                    realized += rem * fill
                    legs.append(("stop", rem, fill)); rem = 0.0
                # breakeven lock (armed by PRIOR bars' peak)
                elif peak >= 3.0 and l <= 0.0:
                    fill = min(0.0, o)
                    realized += rem * fill
                    legs.append(("belock", rem, fill)); rem = 0.0
            else:  # peel runner: trail from running peak (prior bars), floor -12
                trail = max(peak - gb, -12.0)
                if l <= trail:
                    fill = min(trail, o)
                    realized += rem * fill
                    legs.append(("trail", rem, fill)); rem = 0.0
        # ---- upside ----
        if rem > 0:
            if policy == "CURRENT":
                if not tp1 and h >= 6.0:
                    realized += 0.75 * 6.0
                    legs.append(("tp1", 0.75, 6.0))
                    rem -= 0.75; tp1 = True
                if tp1 and rem > 1e-9 and h >= 12.0:
                    fill = max(12.0, min(o, h))  # gap-up open fills at open
                    realized += rem * fill
                    legs.append(("tp2", rem, fill)); rem = 0.0
            else:
                if not tp1 and h >= 6.0:
                    realized += 0.5 * 6.0
                    legs.append(("tp1", 0.5, 6.0))
                    rem = 0.5; tp1 = True
                    # same-bar worst-case trail check: high set peak, then bar
                    # low (order unknown) — assume high-then-low (worse for us)
                    trail = max(h - gb, -12.0)
                    if l <= trail:
                        realized += rem * trail
                        legs.append(("trail_samebar", rem, trail)); rem = 0.0
        peak = max(peak, h)
        if rem <= 1e-9:
            break
    if rem > 1e-9:
        realized += rem * last_c
        legs.append(("horizon", rem, last_c))
        timeout = True
    return {"net": realized, "legs": legs, "timeout": timeout, "peak_bar": peak}


rows = []
nocov = []
for pid, p in pos.items():
    bars = bars_by_pid.get(pid)
    entry_ts = datetime.fromisoformat(p["entry_time"]).timestamp()
    if not bars:
        nocov.append(pid); continue
    # entry-coverage check: first bar within 5 min of entry
    cov = bars[0][0] <= entry_ts + 300
    span_h = (bars[-1][0] - max(bars[0][0], entry_ts)) / 3600
    ep = p["entry_price"]
    r_cur = replay(bars, ep, entry_ts, "CURRENT")
    r_p8 = replay(bars, ep, entry_ts, "PEEL", 8.0)
    r_p5 = replay(bars, ep, entry_ts, "PEEL", 5.0)
    rows.append({
        "pid": pid, "bot": p["bot"], "token": p["token"], "winner": p["winner"],
        "entry_time": p["entry_time"], "peak_rec": p["peak"],
        "realized": p["realized_pp"], "cov": cov, "span_h": round(span_h, 2),
        "cur": r_cur["net"], "p8": r_p8["net"], "p5": r_p5["net"],
        "cur_to": r_cur["timeout"], "p8_to": r_p8["timeout"],
        "peak_bar": r_cur["peak_bar"],
        "p8_legs": r_p8["legs"],
    })

json.dump(rows, open("scratchpad/_tp_replay_rows.json", "w"), indent=1)

def pct(v, q):
    v = sorted(v)
    return v[int(q / 100 * (len(v) - 1))] if v else float("nan")

def block(tag, rs):
    if not rs:
        print(f"{tag}: n=0"); return
    n = len(rs)
    for pol in ["realized", "cur", "p8", "p5"]:
        v = [r[pol] for r in rs]
        tot = sum(v)
        top5 = sum(sorted(v, reverse=True)[:5])
        print(f"  {pol:>9}: total={tot:+8.1f}pp  mean={tot/n:+6.2f}  med={pct(v,50):+6.2f}  "
              f"p75={pct(v,75):+6.2f}  top5={top5:+7.1f}pp ({(top5/tot*100 if tot else 0):.0f}% of total)")

print(f"replayed={len(rows)}  no-bar-coverage={len(nocov)}")
print("no-coverage pids:", [p.split('|')[0][7:] + ':' + pos[p]['token'][:10] for p in nocov])
print(f"entry-covered={sum(1 for r in rows if r['cov'])} of {len(rows)}; "
      f"span>=5.5h: {sum(1 for r in rows if r['span_h']>=5.5)}")

for bot in ["badday_flush", "badday_young_absorb", "badday_adolescent_absorb"]:
    br = [r for r in rows if r["bot"] == bot]
    for lbl, sel in [("WINNERS", True), ("LOSERS", False)]:
        rs = [r for r in br if r["winner"] == sel]
        print(f"\n== {bot} {lbl} n={len(rs)} ==")
        block(bot, rs)

print("\n== HALVES (winners, all bots) ==")
for lbl, lo, hi in [("07-01/02", "2026-07-01", "2026-07-03"),
                    ("07-03+", "2026-07-03", "2026-07-99")]:
    rs = [r for r in rows if r["winner"] and lo <= r["entry_time"] < hi]
    print(f"-- {lbl} n={len(rs)}")
    block(lbl, rs)

print("\n== TOTAL BOOK (all positions, all bots) ==")
block("book", rows)

# sanity: replayed CURRENT vs actual realized on winners
w = [r for r in rows if r["winner"]]
diffs = [r["cur"] - r["realized"] for r in w]
print(f"\nsanity CURRENT-replay minus realized (winners): med={pct(diffs,50):+.2f} "
      f"p25={pct(diffs,25):+.2f} p75={pct(diffs,75):+.2f}")

# top-10 winner detail
print("\ntop-10 winners by recorded peak:")
for r in sorted(w, key=lambda r: -r["peak_rec"])[:10]:
    print(f"  {r['bot'][7:]:>16} {(r['token'] or '?').encode('ascii','replace').decode()[:10]:<10} "
          f"peak_rec={r['peak_rec']:6.1f} peak_bar={r['peak_bar']:7.1f} realized={r['realized']:+6.1f} "
          f"cur={r['cur']:+6.1f} p8={r['p8']:+7.1f} p5={r['p5']:+7.1f} to={r['p8_to']}")
