"""Half-splits + gain/loss structure for the gb5 slice cells."""
import json
from datetime import datetime

pos = json.load(open("scratchpad/_tp_positions.json"))
bars_by_pid = {}
for line in open("scratchpad/_tp_bars.jsonl"):
    d = json.loads(line)
    if d.get("bars"):
        bars_by_pid[d["pid"]] = d["bars"]

SLIP, ULTRA, FEE_PP = 0.703 / 100.0, 0.5 / 100.0, 0.17
frict = lambda p: ((1 + p / 100) * (1 - SLIP - ULTRA) - 1) * 100


def runner(bars, ep, start_ts, peak0, gb):
    P = lambda x: (x / ep - 1.0) * 100.0
    peak, last_c = peak0, None
    for ts, o, h, l, c, vol in bars:
        if ts <= start_ts:
            continue
        o, h, l, c = P(o), P(h), P(l), P(c)
        last_c = c
        trail = max(peak - gb, -12.0)
        if l <= trail:
            return min(trail, o)
        peak = max(peak, h)
    return last_c

elig = []
for p in pos:
    pid = f"{p['bot']}|{p['addr']}|{p['entry_time']}"
    bars = bars_by_pid.get(pid)
    tp1s = [s for s in p["sells"] if s["reason"].startswith("TP1")]
    if not tp1s or not bars:
        continue
    s1 = tp1s[0]
    if s1["pnl"] >= 12.0:
        continue
    t1 = datetime.fromisoformat(s1["time"]).timestamp()
    elig.append((p, bars, s1["pnl"], t1, max(s1["peak"] or 0.0, s1["pnl"])))

for sl, gb in [(0.30, 5.0), (0.50, 5.0), (0.75, 5.0), (0.30, 12.0)]:
    per_bot, halves = {}, {"07-01/02": [0.0, 0], "07-03+": [0.0, 0]}
    gains, losses = [], []
    for p, bars, fill1, t1, peak0 in elig:
        rf = runner(bars, p["entry_price"], t1, peak0, gb)
        if rf is None:
            rf = fill1
        d = sl * fill1 + (1 - sl) * frict(rf) - FEE_PP - p["realized_pp"]
        h = "07-01/02" if p["entry_time"] < "2026-07-03" else "07-03+"
        halves[h][0] += d; halves[h][1] += 1
        b = p["bot"].replace("badday_", "")
        per_bot.setdefault(b, [0.0, 0])
        per_bot[b][0] += d; per_bot[b][1] += 1
        (gains if d > 0 else losses).append(d)
    print(f"s{sl:.2f}/gb{gb:.0f}: halves "
          + " | ".join(f"{k} {v[0]:+.1f} (n={v[1]})" for k, v in halves.items())
          + " || per-bot " + " | ".join(f"{k} {v[0]:+.1f} (n={v[1]})" for k, v in per_bot.items()))
    print(f"           gainers n={len(gains)} sum={sum(gains):+.1f} "
          f"| losers n={len(losses)} sum={sum(losses):+.1f} "
          f"| top3 gains {sorted(gains, reverse=True)[:3]} | worst3 {sorted(losses)[:3]}")
