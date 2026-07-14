# Deliverable 3: median-buy-size band vs realized bounce/death on flush events, per day + pooled.
# Flush = minute close <= -25% vs prior-60m high (>=10 prior bars), 120m refractory per pair.
# medbuy = median tape buy USD (buys >= $1) in [trigger-30m, trigger+5m], need >=8 buys.
# Outcome from trough (min low in 30m after trigger): bounce = max high within 60m >= +15% over trough;
# death = < +5%. Also: TP1-reach = +6% over TRIGGER close within 60m.
import json, os, glob, bisect, statistics as st, collections
from datetime import datetime, timezone

RIP = os.path.dirname(os.path.abspath(__file__))

# bars
bars_by_pair = {}
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if d.get("pair") and d.get("bars"):
        bars_by_pair.setdefault(d["pair"], []).extend(d["bars"])
# tape pairs
tape_pairs = set()
buys = {}   # pair -> [(ep, usd)]
sym_of = {}
for f in glob.glob(os.path.join(RIP, "live_tapes", "tape_*.jsonl")) + glob.glob(os.path.join(RIP, "tape_*.jsonl")):
    for line in open(f, encoding="utf-8"):
        try:
            t = json.loads(line)
        except Exception:
            continue
        if t["ts"] < "2026-07-01":
            continue
        p = t["pair"]
        tape_pairs.add(p)
        sym_of.setdefault(p, t.get("sym", ""))
        if t["kind"] == "buy" and t["volume_usd"] >= 1.0:
            buys.setdefault(p, []).append((datetime.fromisoformat(t["ts"]).timestamp(), t["volume_usd"]))
p12 = {p[:12]: p for p in tape_pairs}
for dd in ("_gt_bars", "_gt_bars_b"):
    for f in glob.glob(os.path.join(RIP, dd, "*.json")):
        p = p12.get(os.path.basename(f).split(".")[0])
        if not p:
            continue
        try:
            b = json.load(open(f))
        except Exception:
            continue
        bl = b if isinstance(b, list) else (b.get("bars") or [])
        if bl:
            bars_by_pair.setdefault(p, []).extend(bl)
for p in list(bars_by_pair):
    u = {int(b[0]): b for b in bars_by_pair[p]}
    bars_by_pair[p] = sorted(u.values(), key=lambda x: x[0])
for p in buys:
    buys[p].sort()

# tape coverage span per pair (only score flushes where tape had eyes)
tape_span = {}
for p, bl in buys.items():
    tape_span[p] = (bl[0][0], bl[-1][0])

events = []
for p in tape_pairs:
    bl = bars_by_pair.get(p)
    sp = tape_span.get(p)
    if not bl or not sp:
        continue
    ts = [b[0] for b in bl]
    last_trig = 0
    for i in range(len(bl)):
        ep = bl[i][0]
        if ep < sp[0] - 300 or ep > sp[1]:
            continue
        if ep - last_trig < 7200:
            continue
        j0 = bisect.bisect_left(ts, ep - 3600)
        prior = bl[j0:i]
        if len(prior) < 10:
            continue
        hi = max(b[2] for b in prior)
        c = bl[i][4]
        if not hi or hi <= 0 or not c:
            continue
        if c / hi - 1 > -0.25:
            continue
        # trough within 30m
        k = bisect.bisect_right(ts, ep + 1800)
        seg = bl[i:k]
        lows = [(b[3], b[0]) for b in seg if b[3] and b[3] > 0]
        if not lows:
            continue
        trough, t_ts = min(lows)
        # medbuy in [ep-1800, ep+300]
        bb = buys.get(p, [])
        lo = bisect.bisect_left(bb, (ep - 1800, -1.0))
        hi_i = bisect.bisect_right(bb, (ep + 300, 1e18))
        w_buys = [u for _, u in bb[lo:hi_i]]
        if len(w_buys) < 3:
            last_trig = ep
            continue
        mb = st.median(w_buys)
        # outcome
        k2 = bisect.bisect_right(ts, t_ts + 3600)
        i_t = bisect.bisect_left(ts, t_ts)
        fwd = bl[i_t + 1:k2]
        if len(fwd) < 5:
            last_trig = ep
            continue  # no runway (tape/bar end)
        fmax_tr = 100 * (max(b[2] for b in fwd) / trough - 1)
        # TP1 reach from trigger close
        fwd2 = bl[i + 1:bisect.bisect_right(ts, ep + 3600)]
        tp1 = any(b[2] >= c * 1.06 for b in fwd2) if fwd2 else None
        day = datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%Y-%m-%d")
        events.append({"pair": p, "sym": sym_of.get(p, ""), "day": day, "ep": ep,
                       "drop": round(100 * (c / hi - 1), 1), "medbuy": round(mb, 2),
                       "n_buys": len(w_buys), "fmax_tr": round(fmax_tr, 1), "tp1_from_trig": tp1})
        last_trig = ep

print("flush events:", len(events))
json.dump(events, open(os.path.join(RIP, "flush_events_0704.json"), "w"))

def bandof(mb, n=99):
    b = "<$8" if mb < 8 else ("$8-13" if mb < 13 else ("$13-26" if mb < 26 else ">=$26"))
    return b

def report(evts, label):
    print("\n--- %s (n=%d) ---" % (label, len(evts)))
    bands = collections.defaultdict(list)
    for e in evts:
        bands[bandof(e["medbuy"])].append(e)
    print("%-7s %4s %8s %8s %8s %10s %8s %6s" % ("band", "n", "bounce%", "death%", "medfmax", "tp1reach%", "medbuy", "medN"))
    for b in ["<$8", "$8-13", "$13-26", ">=$26"]:
        ev = bands[b]
        if not ev:
            print("%-7s %4d" % (b, 0)); continue
        bounce = sum(1 for e in ev if e["fmax_tr"] >= 15)
        death = sum(1 for e in ev if e["fmax_tr"] < 5)
        tp1n = [e for e in ev if e["tp1_from_trig"] is not None]
        tp1 = sum(1 for e in tp1n if e["tp1_from_trig"])
        print("%-7s %4d %7.0f%% %7.0f%% %8.1f %9.0f%% %8.1f %6.0f" % (
            b, len(ev), 100 * bounce / len(ev), 100 * death / len(ev),
            st.median(e["fmax_tr"] for e in ev), 100 * tp1 / len(tp1n) if tp1n else -1,
            st.median(e["medbuy"] for e in ev), st.median(e["n_buys"] for e in ev)))

for day in ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"]:
    report([e for e in events if e["day"] == day], day)
report([e for e in events if e["day"] <= "2026-07-03"], "POOLED 07-01..03")
report([e for e in events if e["day"] == "2026-07-03" or e["day"] == "2026-07-04"], "07-03+04")

# our 07-03 structural-loss tokens
print("\n--- our 07-03 loss tokens ---")
for e in events:
    s = e["sym"].lower()
    if any(k in s for k in ("rush", "bindy", "martolexx", "usa")):
        print(e["day"], e["sym"], "drop=%.1f medbuy=%.2f n=%d fmax=%.1f tp1=%s" % (
            e["drop"], e["medbuy"], e["n_buys"], e["fmax_tr"], e["tp1_from_trig"]))
