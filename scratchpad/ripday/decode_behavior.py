# Q2: behavior decode for current-regime winners vs baseline (all human multi-pair wallets)
# axes: entry timing vs dip/pump state, hold time, exit shape, token selection, avoidance
import json, os, glob, bisect, statistics as st, collections
from datetime import datetime

RIP = os.path.dirname(os.path.abspath(__file__))
led = json.load(open(os.path.join(RIP, "ledger2_wallets.json")))
win = json.load(open(os.path.join(RIP, "winners_current.json")))
winners = set(win["winners"].keys())

def iso2ep(s):
    return datetime.fromisoformat(s).timestamp()

# ---- bars ----
bars_by_pair = {}
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    d = json.load(open(f))
    if d.get("pair") and d.get("bars"):
        bars_by_pair.setdefault(d["pair"], []).extend(d["bars"])
pairs_all = set()
for w, eps in led.items():
    for e in eps:
        pairs_all.add(e["pair"])
p12 = {p[:12]: p for p in pairs_all}
for f in glob.glob(os.path.join(RIP, "_gt_bars", "*.json")):
    stem = os.path.basename(f).split(".")[0]
    p = p12.get(stem)
    if not p:
        continue
    b = json.load(open(f))
    if isinstance(b, list) and b:
        bars_by_pair.setdefault(p, []).extend(b)
for p in bars_by_pair:
    u = {int(b[0]): b for b in bars_by_pair[p]}
    bars_by_pair[p] = sorted(u.values(), key=lambda x: x[0])
bar_ts = {p: [b[0] for b in bars_by_pair[p]] for p in bars_by_pair}

def bar_at(p, ep):
    ts = bar_ts.get(p)
    if not ts:
        return None
    i = bisect.bisect_right(ts, ep) - 1
    if i < 0:
        return None
    if ep - ts[i] > 1800:
        return None
    return i

# ---- token age ----
age_src = {}
try:
    tm = json.load(open(os.path.join(RIP, "token_meta.json")))
    for p, v in tm.items():
        if v.get("pool_created_at"):
            age_src[p] = datetime.fromisoformat(v["pool_created_at"].replace("Z", "+00:00")).timestamp()
except Exception:
    pass
try:
    pc = json.load(open(os.path.join(RIP, "_pair_created_cache.json")))
    for p, v in pc.items():
        if v:
            age_src.setdefault(p, float(v))
except Exception:
    pass
try:
    pc2 = json.load(open(os.path.join(RIP, "_pair_created_cache2.json")))
    for p, v in pc2.items():
        if v:
            age_src.setdefault(p, float(v))
except Exception:
    pass
# fallback: first bar ts (upper bound on age)
for p, bl in bars_by_pair.items():
    if p not in age_src and bl:
        age_src[p] = None  # unknown; do NOT fake with first bar (harvest window start)

# ---- liq/meta ----
liq = {}
try:
    ds = json.load(open(os.path.join(RIP, "_ds_state_cache.json")))
    for k, v in ds.items():
        if isinstance(v, dict) and v.get("liq") is not None:
            liq[k] = v["liq"]
except Exception:
    pass

# ---- tape flow per pair (for absorption around entries) ----
flows = {}  # pair -> sorted [(ep, signed_usd)]
for f in glob.glob(os.path.join(RIP, "live_tapes", "tape_*.jsonl")) + glob.glob(os.path.join(RIP, "tape_*.jsonl")):
    for line in open(f, encoding="utf-8"):
        try:
            t = json.loads(line)
        except Exception:
            continue
        if t["ts"] < "2026-07-01":
            continue
        ep = iso2ep(t["ts"])
        flows.setdefault(t["pair"], []).append((ep, t["volume_usd"] if t["kind"] == "buy" else -t["volume_usd"]))
for p in flows:
    flows[p].sort()

def net_flow(p, ep, back=300):
    fl = flows.get(p)
    if not fl:
        return None
    lo = bisect.bisect_left(fl, (ep - back, -1e18))
    hi = bisect.bisect_right(fl, (ep, 1e18))
    seg = fl[lo:hi]
    if not seg:
        return 0.0
    return sum(x[1] for x in seg)

# ---- feature extraction per BUY ----
def buy_features(w, e):
    """features for each buy ts in episode e"""
    p = e["pair"]
    out = []
    for bts in e["buy_ts"]:
        ep = iso2ep(bts)
        i = bar_at(p, ep)
        if i is None:
            continue
        bl = bars_by_pair[p]
        px = bl[i][4]
        if not px or px <= 0:
            continue
        # prior 60m window
        j0 = bisect.bisect_left(bar_ts[p], ep - 3600)
        prior = bl[j0:i + 1]
        hi60 = max(b[2] for b in prior) if prior else px
        lo60 = min(b[3] for b in prior) if prior else px
        dip60 = 100 * (px / hi60 - 1) if hi60 > 0 else 0
        posr = (px - lo60) / (hi60 - lo60) if hi60 > lo60 else 0.5
        # prior 10m momentum
        j1 = bisect.bisect_left(bar_ts[p], ep - 600)
        m10 = 100 * (px / bl[j1][4] - 1) if j1 < len(bl) and bl[j1][4] > 0 else 0
        # forward 60m
        k1 = bisect.bisect_right(bar_ts[p], ep + 3600)
        fwd = bl[i + 1:k1]
        fmax = 100 * (max(b[2] for b in fwd) / px - 1) if fwd else None
        fmin = 100 * (min(b[3] for b in fwd) / px - 1) if fwd else None
        age_ep = age_src.get(p)
        age_h = (ep - age_ep) / 3600 if age_ep else None
        nf = net_flow(p, ep, 300)
        out.append({"w": w, "pair": p, "sym": e["sym"], "ts": bts, "px": px, "dip60": dip60,
                    "pos_range60": posr, "mom10": m10, "fwd_max60": fmax, "fwd_min60": fmin,
                    "age_h": age_h, "nf5m": nf, "usd": None})
    return out

MIN_BUY = 20.0
win_buys, base_buys = [], []
win_eps, base_eps = [], []
for w, eps in led.items():
    for e in eps:
        if e["buy_usd"] < MIN_BUY:
            continue
        n_pairs = sum(1 for x in eps if x["buy_usd"] >= MIN_BUY)
        if w in winners:
            win_eps.append((w, e))
        elif n_pairs >= 2:
            base_eps.append((w, e))

print("winner episodes:", len(win_eps), "baseline episodes:", len(base_eps))
import random
random.seed(7)
if len(base_eps) > 3000:
    base_eps = random.sample(base_eps, 3000)
for w, e in win_eps:
    win_buys += buy_features(w, e)
for w, e in base_eps:
    base_buys += buy_features(w, e)
print("winner buys w/ bars:", len(win_buys), "baseline buys w/ bars:", len(base_buys))

def dist(name, rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        print("%s: no data" % name)
        return
    vals.sort()
    q = lambda f: vals[min(len(vals) - 1, int(f * len(vals)))]
    print("%s n=%d p10=%.1f p25=%.1f med=%.1f p75=%.1f p90=%.1f mean=%.1f" % (
        name, len(vals), q(.1), q(.25), st.median(vals), q(.75), q(.9), st.mean(vals)))

print("\n=== ENTRY STATE: winners vs baseline ===")
for k in ["dip60", "pos_range60", "mom10", "age_h", "nf5m", "fwd_max60", "fwd_min60"]:
    dist("WIN  " + k, win_buys, k)
    dist("BASE " + k, base_buys, k)
    print()

json.dump({"win_buys": win_buys, "base_buys": base_buys}, open(os.path.join(RIP, "behavior_buys.json"), "w"))

# ---- hold + exit shape per winner episode ----
def episode_shape(w, e):
    p = e["pair"]
    if not e["buy_ts"] or not e["sell_ts"]:
        return None
    b0 = iso2ep(e["buy_ts"][0])
    sells = sorted(iso2ep(x) for x in e["sell_ts"] if x >= e["buy_ts"][0])
    if not sells:
        return None
    hold_first = (sells[0] - b0) / 60
    hold_last = (sells[-1] - b0) / 60
    # drawdown endured between first buy and first sell
    i0, i1 = bar_at(p, b0), bar_at(p, sells[0])
    dd = None
    if i0 is not None and i1 is not None and i1 >= i0:
        bl = bars_by_pair[p]
        px0 = bl[i0][4]
        if px0 > 0:
            dd = 100 * (min(b[3] for b in bl[i0:i1 + 1]) / px0 - 1)
    return {"w": w, "sym": e["sym"], "n_buys": e["n_buys"], "n_sells": e["n_sells"],
            "hold_first_m": hold_first, "hold_last_m": hold_last, "dd_before_first_sell": dd,
            "net": e["net"], "buy_usd": e["buy_usd"]}

shapes = [s for w, e in win_eps if (s := episode_shape(w, e))]
bshapes = [s for w, e in base_eps if (s := episode_shape(w, e))]
print("\n=== HOLD/EXIT SHAPE (episodes with sells) ===")
for k in ["hold_first_m", "hold_last_m", "dd_before_first_sell", "n_buys", "n_sells"]:
    dist("WIN  " + k, shapes, k)
    dist("BASE " + k, bshapes, k)
    print()
open_bags_w = sum(1 for w, e in win_eps if e["n_sells"] == 0)
open_bags_b = sum(1 for w, e in base_eps if e["n_sells"] == 0)
print("open bags (no sells in window): WIN %d/%d  BASE %d/%d" % (open_bags_w, len(win_eps), open_bags_b, len(base_eps)))
json.dump({"win_shapes": shapes, "base_shapes": bshapes}, open(os.path.join(RIP, "behavior_shapes.json"), "w"))
