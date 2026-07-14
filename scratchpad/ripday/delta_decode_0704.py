# Delta checks 07-03 vs 07-01/02 on the 4 headline findings + behavior rotation.
# Uses ledger3 (day-scoped episodes) + winners_by_day.json + bars + tape flows.
import json, os, glob, bisect, statistics as st, collections
from datetime import datetime, timezone

RIP = os.path.dirname(os.path.abspath(__file__))
led = json.load(open(os.path.join(RIP, "ledger3_wallets.json")))
wbd = json.load(open(os.path.join(RIP, "winners_by_day.json")))
DAYS = ["2026-07-01", "2026-07-02", "2026-07-03"]

def iso2ep(s):
    return datetime.fromisoformat(s).timestamp()

# ---- bars ----
bars_by_pair = {}
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if d.get("pair") and d.get("bars"):
        bars_by_pair.setdefault(d["pair"], []).extend(d["bars"])
pairs_all = {e["pair"] for eps in led.values() for e in eps}
p12 = {p[:12]: p for p in pairs_all}
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
for p in bars_by_pair:
    u = {int(b[0]): b for b in bars_by_pair[p]}
    bars_by_pair[p] = sorted(u.values(), key=lambda x: x[0])
bar_ts = {p: [b[0] for b in bars_by_pair[p]] for p in bars_by_pair}

def bar_at(p, ep, tol=1800):
    ts = bar_ts.get(p)
    if not ts:
        return None
    i = bisect.bisect_right(ts, ep) - 1
    if i < 0 or ep - ts[i] > tol:
        return None
    return i

# ---- ages ----
age_src = {}
try:
    tm = json.load(open(os.path.join(RIP, "token_meta.json")))
    for p, v in tm.items():
        if v.get("pool_created_at"):
            age_src[p] = datetime.fromisoformat(v["pool_created_at"].replace("Z", "+00:00")).timestamp()
except Exception:
    pass
for fn in ("_pair_created_cache.json", "_pair_created_cache2.json"):
    try:
        pc = json.load(open(os.path.join(RIP, fn)))
        for p, v in pc.items():
            if v:
                age_src.setdefault(p, float(v))
    except Exception:
        pass

# ---- flows (net 5m) ----
flows = {}
for f in glob.glob(os.path.join(RIP, "live_tapes", "tape_*.jsonl")) + glob.glob(os.path.join(RIP, "tape_*.jsonl")):
    for line in open(f, encoding="utf-8"):
        try:
            t = json.loads(line)
        except Exception:
            continue
        if t["ts"] < "2026-07-01":
            continue
        flows.setdefault(t["pair"], []).append((iso2ep(t["ts"]), t["volume_usd"] if t["kind"] == "buy" else -t["volume_usd"]))
for p in flows:
    flows[p].sort()

def net_flow(p, ep, back=300):
    fl = flows.get(p)
    if not fl:
        return None
    lo = bisect.bisect_left(fl, (ep - back, -1e18))
    hi = bisect.bisect_right(fl, (ep, 1e18))
    return sum(x[1] for x in fl[lo:hi]) if hi > lo else 0.0

def dist(name, vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        print("%s: n=0" % name); return
    q = lambda f: vals[min(len(vals) - 1, int(f * len(vals)))]
    print("%s n=%d p10=%.1f p25=%.1f med=%.1f p75=%.1f p90=%.1f mean=%.1f" % (
        name, len(vals), q(.1), q(.25), st.median(vals), q(.75), q(.9), st.mean(vals)))

MIN_BUY = 20.0
for day in DAYS:
    winners = set(wbd[day]["winners"])
    win_eps, base_eps = [], []
    for w, eps in led.items():
        n_pairs_day = sum(1 for x in eps if x["day"] == day and x["buy_usd"] >= MIN_BUY)
        for e in eps:
            if e["day"] != day or e["buy_usd"] < MIN_BUY or e.get("no_px"):
                continue
            if w in winners:
                win_eps.append((w, e))
            elif n_pairs_day >= 2:
                base_eps.append((w, e))
    print("\n############ %s: winner eps=%d base eps=%d ############" % (day, len(win_eps), len(base_eps)))

    # ---------- FINDING 1: endured wick before first sell ----------
    def wick(e):
        p = e["pair"]
        if not e["buy_ts"] or not e["sell_ts"]:
            return None
        b0 = iso2ep(e["buy_ts"][0])
        s0 = min(iso2ep(x) for x in e["sell_ts"])
        i0, i1 = bar_at(p, b0), bar_at(p, s0)
        if i0 is None or i1 is None or i1 < i0:
            return None
        bl = bars_by_pair[p]
        px0 = bl[i0][4]
        if not px0 or px0 <= 0:
            return None
        return 100 * (min(b[3] for b in bl[i0:i1 + 1]) / px0 - 1)
    dist("F1 WIN  dd_before_first_sell", [wick(e) for w, e in win_eps])
    dist("F1 BASE dd_before_first_sell", [wick(e) for w, e in base_eps])

    # ---------- FINDING 2: age band + hour of day ----------
    def band(a):
        if a is None: return "unk"
        if a < 2: return "<2h"
        if a < 6: return "2-6h"
        if a < 24: return "6-24h"
        if a < 72: return "24-72h"
        return ">72h"
    for label, eset in (("WIN", win_eps), ("BASE", base_eps)):
        bb = collections.defaultdict(lambda: [0, 0.0, 0.0])  # n, net, buy_usd
        hours = collections.Counter()
        nbuys = 0
        for w, e in eset:
            ep0 = iso2ep(e["first_buy"])
            a = (ep0 - age_src[e["pair"]]) / 3600 if e["pair"] in age_src else None
            b = band(a)
            bb[b][0] += 1; bb[b][1] += e["net"]; bb[b][2] += e["buy_usd"]
            for bts in e["buy_ts"]:
                hours[int(bts[11:13])] += 1
                nbuys += 1
        row = []
        for b in ["<2h", "2-6h", "6-24h", "24-72h", ">72h", "unk"]:
            n, net, bu = bb[b]
            row.append("%s: n=%d ret=%+.1f%%" % (b, n, 100 * net / bu if bu else 0))
        print("F2 %s age bands: %s" % (label, " | ".join(row)))
        h1422 = sum(v for h, v in hours.items() if 14 <= h <= 22)
        h0413 = sum(v for h, v in hours.items() if 4 <= h <= 13)
        h2301 = sum(v for h, v in hours.items() if h >= 23 or h <= 1)
        if nbuys:
            print("F2 %s hours: buys=%d  14-22UTC=%.0f%%  04-13=%.1f%%  23-01=%.1f%%" % (
                label, nbuys, 100 * h1422 / nbuys, 100 * h0413 / nbuys, 100 * h2301 / nbuys))

    # ---------- FINDING 3: scratch machine exits ----------
    sell_rets, sell_usd_w = [], []
    for w, e in win_eps:
        vwap = e.get("buy_vwap")
        if not vwap:
            continue
        for px, usd in zip(e["sell_px"], e["sell_usd_list"]):
            if px and usd >= 2:
                r = 100 * (px / vwap - 1)
                if -95 < r < 500:
                    sell_rets.append(r); sell_usd_w.append((r, usd))
    dist("F3 WIN sell ret vs entry VWAP (covered, dust<$2 excl)", sell_rets)
    if sell_usd_w:
        tot = sum(u for _, u in sell_usd_w)
        bks = [("<-12", lambda r: r < -12), ("-12..0", lambda r: -12 <= r < 0), ("0..+6", lambda r: 0 <= r < 6),
               ("+6..+12", lambda r: 6 <= r < 12), (">=+12", lambda r: r >= 12)]
        print("F3 USD-weighted: " + " | ".join("%s: %.0f%%" % (nm, 100 * sum(u for r, u in sell_usd_w if fn(r)) / tot) for nm, fn in bks))
    # loss cuts on closed losing episodes
    lc_depth, lc_time = [], []
    closed = 0
    for w, e in win_eps:
        if e["frac_sold"] < 0.8:
            continue
        closed += 1
        rpct = 100 * e["realized"] / e["buy_usd"]
        if rpct < 0 and e["sell_ts"]:
            lc_depth.append(rpct)
            lc_time.append((max(iso2ep(x) for x in e["sell_ts"]) - iso2ep(e["first_buy"])) / 60)
    print("F3 closed win eps=%d, losers=%d" % (closed, len(lc_depth)))
    dist("F3 WIN loss-cut depth (realized %)", lc_depth)
    dist("F3 WIN loss-cut time first-buy->last-sell (min)", lc_time)
    creal = [100 * e["realized"] / e["buy_usd"] for w, e in win_eps if e["frac_sold"] >= 0.8]
    dist("F3 WIN closed realized ret%", creal)
    if creal:
        print("F3 WIN closed WR: %d/%d = %.0f%%" % (sum(1 for r in creal if r > 0), len(creal), 100 * sum(1 for r in creal if r > 0) / len(creal)))

    # ---------- rotation extras: entry state ----------
    def entry_feats(e):
        p = e["pair"]
        ep0 = iso2ep(e["first_buy"])
        i = bar_at(p, ep0)
        if i is None:
            return None
        bl = bars_by_pair[p]
        px = bl[i][4]
        if not px or px <= 0:
            return None
        j0 = bisect.bisect_left(bar_ts[p], ep0 - 3600)
        prior = bl[j0:i + 1]
        hi60 = max(b[2] for b in prior) if prior else px
        dip60 = 100 * (px / hi60 - 1) if hi60 > 0 else 0
        j1 = bisect.bisect_left(bar_ts[p], ep0 - 600)
        m10 = 100 * (px / bl[j1][4] - 1) if j1 < len(bl) and bl[j1][4] and bl[j1][4] > 0 else 0
        k1 = bisect.bisect_right(bar_ts[p], ep0 + 3600)
        fwd = bl[i + 1:k1]
        fmax = 100 * (max(b[2] for b in fwd) / px - 1) if fwd else None
        nf = net_flow(p, ep0, 300)
        return dip60, m10, nf, fmax
    for label, eset in (("WIN", win_eps), ("BASE", base_eps)):
        rows = [entry_feats(e) for w, e in eset]
        rows = [r for r in rows if r]
        dist("RX %s dip60" % label, [r[0] for r in rows])
        dist("RX %s mom10" % label, [r[1] for r in rows])
        dist("RX %s nf5m" % label, [r[2] for r in rows])
        dist("RX %s fwd_max60" % label, [r[3] for r in rows])
    # shape: buys/sells per episode, hold, rebuy after sell
    for label, eset in (("WIN", win_eps),):
        dist("RX %s n_buys/ep" % label, [e["n_buys"] for w, e in eset])
        dist("RX %s n_sells/ep" % label, [e["n_sells"] for w, e in eset])
        holds = []
        rebuy = 0; sold = 0
        for w, e in eset:
            if e["sell_ts"]:
                sold += 1
                holds.append((min(iso2ep(x) for x in e["sell_ts"]) - iso2ep(e["first_buy"])) / 60)
                s0 = min(e["sell_ts"])
                if any(b > s0 for b in e["buy_ts"]):
                    rebuy += 1
        dist("RX %s hold to first sell (min)" % label, holds)
        if sold:
            print("RX %s rebuy-after-sell: %d/%d = %.0f%%  open bags: %d/%d" % (
                label, rebuy, sold, 100 * rebuy / sold, sum(1 for w, e in eset if not e["sell_ts"]), len(eset)))
print("\nDONE")
