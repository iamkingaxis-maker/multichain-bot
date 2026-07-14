# Part C: predicate variant grid with close-based outcomes + drawdown, on the same hourly grid.
import json, os, glob, bisect, statistics as st, collections
from datetime import datetime, timezone

RIP = os.path.dirname(os.path.abspath(__file__))
led = json.load(open(os.path.join(RIP, "ledger3_wallets.json")))
bars_by_pair = {}
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    try: d = json.load(open(f))
    except Exception: continue
    if d.get("pair") and d.get("bars"):
        bars_by_pair.setdefault(d["pair"], []).extend(d["bars"])
pairs_all = {e["pair"] for eps in led.values() for e in eps}
p12 = {p[:12]: p for p in pairs_all}
for dd in ("_gt_bars", "_gt_bars_b"):
    for f in glob.glob(os.path.join(RIP, dd, "*.json")):
        p = p12.get(os.path.basename(f).split(".")[0])
        if not p: continue
        try: b = json.load(open(f))
        except Exception: continue
        bl = b if isinstance(b, list) else (b.get("bars") or [])
        if bl: bars_by_pair.setdefault(p, []).extend(bl)
for p in bars_by_pair:
    u = {int(b[0]): b for b in bars_by_pair[p]}
    bars_by_pair[p] = sorted(u.values(), key=lambda x: x[0])
age_src = {}
try:
    tm = json.load(open(os.path.join(RIP, "token_meta.json")))
    for p, v in tm.items():
        if v.get("pool_created_at"):
            age_src[p] = datetime.fromisoformat(v["pool_created_at"].replace("Z", "+00:00")).timestamp()
except Exception: pass
for fn in ("_pair_created_cache.json", "_pair_created_cache2.json"):
    try:
        pc = json.load(open(os.path.join(RIP, fn)))
        for p, v in pc.items():
            if v: age_src.setdefault(p, float(v))
    except Exception: pass

def day_of(t): return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")

rows = []  # (pair, t, feats, outcomes)
for p in pairs_all:
    bl = bars_by_pair.get(p)
    if not bl or p not in age_src: continue
    ts = [b[0] for b in bl]
    hv = collections.defaultdict(float)
    for b in bl: hv[int(b[0] // 3600)] += b[5]
    hours = sorted(hv.keys())
    if not hours: continue
    h0, h1 = hours[0], hours[-1]
    for H in range(h0 + 12, h1 - 5):
        t = (H + 1) * 3600
        if (t - age_src[p]) / 3600.0 <= 72: continue
        look = [hv.get(h, 0.0) for h in range(max(h0, H - 48), H)]
        if len(look) < 12: continue
        prior24 = [hv.get(h, 0.0) for h in range(max(h0, H - 24), H)]
        avg24 = sum(prior24) / len(prior24)
        peak48 = max(look)
        v_now = hv.get(H, 0.0)
        if v_now < 500: continue
        i = bisect.bisect_right(ts, t) - 1
        if i < 0 or t - ts[i] > 3600: continue
        px = bl[i][4]
        lo48 = bisect.bisect_left(ts, t - 48 * 3600)
        hi48 = max(b[2] for b in bl[lo48:i + 1])
        j = bisect.bisect_right(ts, t + 6 * 3600)
        if j <= i + 1: continue
        seg = bl[i + 1:j]
        fwd_hi = 100.0 * (max(b[2] for b in seg) / px - 1)
        fwd_cl = 100.0 * (max(b[4] for b in seg) / px - 1)   # max close = holdable
        fwd_end = 100.0 * (seg[-1][4] / px - 1)
        fwd_dd = 100.0 * (min(b[3] for b in seg) / px - 1)
        rows.append((p, t, dict(peak48=peak48, dorm=(avg24 / peak48 if peak48 else None),
                                ramp=(v_now / avg24 if avg24 else None), v_now=v_now,
                                base=px / hi48),
                     dict(hi=fwd_hi, cl=fwd_cl, end=fwd_end, dd=fwd_dd)))

def report(name, pred):
    m = [(p, t, o) for p, t, f, o in rows if pred(f)]
    u = [(p, t, o) for p, t, f, o in rows if not pred(f)]
    def tok_first(evs):
        d = {}
        for p, t, o in sorted(evs, key=lambda x: x[1]): d.setdefault(p, o)
        return d
    mt, ut0 = tok_first(m), tok_first(u)
    ut = {p: o for p, o in ut0.items() if p not in mt}
    def stats(d):
        if not d: return "n=0"
        cl = [o["cl"] for o in d.values()]; dd = [o["dd"] for o in d.values()]; end = [o["end"] for o in d.values()]
        return "n=%d hitCL15=%.0f%% medCL=%+.1f medEND=%+.1f medDD=%+.1f" % (
            len(d), 100.0 * sum(1 for c in cl if c >= 15) / len(d), st.median(cl), st.median(end), st.median(dd))
    print("%s\n  MATCH tok: %s\n  NOMATCH tok: %s\n  events m=%d u=%d hitCL15 m=%.0f%% u=%.0f%%" % (
        name, stats(mt), stats(ut), len(m), len(u),
        100.0 * sum(1 for _, _, o in m if o["cl"] >= 15) / len(m) if m else 0,
        100.0 * sum(1 for _, _, o in u if o["cl"] >= 15) / len(u) if u else 0))
    # per-day matched tokens
    pd = collections.defaultdict(dict)
    for p, t, o in sorted(m, key=lambda x: x[1]): pd[day_of(t)].setdefault(p, o)
    print("  per-day: " + " | ".join("%s n=%d hit=%d%% medEND=%+.0f" % (
        d, len(v), round(100.0 * sum(1 for o in v.values() if o["cl"] >= 15) / len(v)),
        st.median([o["end"] for o in v.values()])) for d, v in sorted(pd.items())))

base_pred = lambda f: (f["peak48"] >= 25000 and f["dorm"] is not None and f["dorm"] <= 0.35
                       and f["v_now"] >= 5000 and f["ramp"] is not None and f["ramp"] >= 1.5
                       and f["base"] >= 0.55)
report("P0 (peak48>=25k dorm<=.35 vnow>=5k ramp>=1.5 base>=.55)", base_pred)
report("P1 ramp>=3", lambda f: base_pred(f) and f["ramp"] >= 3)
report("P2 vnow>=10k", lambda f: base_pred(f) and f["v_now"] >= 10000)
report("P3 dorm<=.20", lambda f: base_pred(f) and f["dorm"] <= 0.20)
report("P4 ramp 1.5-12 (no blowoff chase)", lambda f: base_pred(f) and f["ramp"] <= 12)
report("P5 base>=.70", lambda f: base_pred(f) and f["base"] >= 0.70)
report("P6 P4+P2", lambda f: base_pred(f) and f["ramp"] <= 12 and f["v_now"] >= 10000)
