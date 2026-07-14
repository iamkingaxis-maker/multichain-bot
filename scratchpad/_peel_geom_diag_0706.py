"""Diagnostics: pipeline identity check vs original replay (+72.0 expected),
true available bar coverage from TP1, exit-time distributions per gb,
ex-top-token totals (fat-tail honesty)."""
import json
from datetime import datetime

pos = json.load(open("scratchpad/_tp_positions.json"))
bars_by_pid = {}
for line in open("scratchpad/_tp_bars.jsonl"):
    d = json.loads(line)
    if d.get("bars"):
        bars_by_pid[d["pid"]] = d["bars"]

SLIP = 0.703 / 100.0
ULTRA = 0.5 / 100.0
FEE_PP = 0.17


def runner(bars, ep, start_ts, peak0, gb, maxhold_s=None):
    P = lambda x: (x / ep - 1.0) * 100.0
    peak, last_c, last_ts = peak0, None, None
    for ts, o, h, l, c, vol in bars:
        if ts <= start_ts:
            continue
        if maxhold_s is not None and ts > start_ts + maxhold_s:
            if last_c is None:
                return P(o), "maxhold", (ts - start_ts) / 60.0
            return last_c, "maxhold", (last_ts - start_ts) / 60.0
        o, h, l, c = P(o), P(h), P(l), P(c)
        last_c, last_ts = c, ts
        trail = max(peak - gb, -12.0)
        if l <= trail:
            return min(trail, o), "trail", (ts - start_ts) / 60.0
        peak = max(peak, h)
    if last_c is None:
        return None, None, 0.0
    return last_c, "open", (last_ts - start_ts) / 60.0


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

# 1. identity check: zero friction, s0.50/gb5/none -> expect +72.0
tot = 0.0
for p, bars, fill1, t1, peak0 in elig:
    rf, how, mins = runner(bars, p["entry_price"], t1, peak0, 5.0)
    if rf is None:
        rf = fill1
    tot += (0.5 * fill1 + 0.5 * rf) - p["realized_pp"]
print(f"IDENTITY CHECK s0.50/gb5 zero-friction delta = {tot:+.1f}pp "
      f"(original replay headline: +72.0), n={len(elig)}")

# 2. true available bar coverage from TP1
covs = sorted((bars[-1][0] - t1) / 3600.0 for _, bars, _, t1, _ in elig)
n = len(covs)
print(f"AVAILABLE coverage from TP1 (h): min {covs[0]:.2f} p10 {covs[int(.1*n)]:.2f} "
      f"med {covs[n//2]:.2f} max {covs[-1]:.2f} | >=3h {sum(1 for c in covs if c>=3)}/{n} "
      f">=5h {sum(1 for c in covs if c>=5)}/{n}")

# 3. exit-time distribution per gb (no maxhold)
for gb in [5.0, 8.0, 12.0]:
    times = []
    for p, bars, fill1, t1, peak0 in elig:
        rf, how, mins = runner(bars, p["entry_price"], t1, peak0, gb)
        if rf is not None:
            times.append(mins)
    times.sort()
    m = len(times)
    print(f"gb{gb:.0f} runner exit-time (min): med {times[m//2]:.0f} p75 {times[int(.75*m)]:.0f} "
          f"p90 {times[int(.9*m)]:.0f} max {times[-1]:.0f} | >45min: {sum(1 for t in times if t>45)}/{m}")

# 4. ex-top-token totals per cell (fat-tail honesty) + immediate-exit share
def frict(pnl):
    return ((1.0 + pnl / 100.0) * (1.0 - SLIP - ULTRA) - 1.0) * 100.0

print(f"\n{'cell':<18} {'delta':>8} {'ex-best-token':>14} {'ex-top-pos':>11} {'exit<=2min':>10}")
for sl in [0.30, 0.50, 0.75]:
    for gb, hl, mh in [(5.0, "none", None), (8.0, "none", None),
                       (12.0, "none", None), (12.0, "3h", 3*3600)]:
        deltas, tok_delta, fast = [], {}, 0
        for p, bars, fill1, t1, peak0 in elig:
            rf, how, mins = runner(bars, p["entry_price"], t1, peak0, gb, mh)
            if rf is None:
                rf, mins = fill1, 0.0
            vpp = sl * fill1 + (1 - sl) * frict(rf) - FEE_PP
            d = vpp - p["realized_pp"]
            deltas.append(d)
            tok = p["token"] or p["addr"][:6]
            tok_delta[tok] = tok_delta.get(tok, 0.0) + d
            if mins <= 2:
                fast += 1
        tot = sum(deltas)
        best_tok = max(tok_delta.values())
        top_pos = max(deltas)
        print(f"s{sl:.2f}/gb{gb:.0f}/{hl:<5} {tot:+8.1f} {tot-best_tok:+14.1f} "
              f"{tot-top_pos:+11.1f} {fast:>7}/{len(deltas)}")
