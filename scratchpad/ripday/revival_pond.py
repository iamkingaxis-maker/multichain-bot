# REVIVAL-POND definition study (07-04). ANALYSIS ONLY.
# Part A: characterize 07-03 winner episodes on >72h tokens at winner-entry time.
# Part B: base rates of a candidate predicate on all >72h tokens.
import json, os, glob, bisect, statistics as st, collections
from datetime import datetime, timezone

RIP = os.path.dirname(os.path.abspath(__file__))
led = json.load(open(os.path.join(RIP, "ledger3_wallets.json")))
wbd = json.load(open(os.path.join(RIP, "winners_by_day.json")))

def iso2ep(s): return datetime.fromisoformat(s).timestamp()

# ---- bars (same load as delta_decode_0704) ----
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
bar_ts = {p: [b[0] for b in bars_by_pair[p]] for p in bars_by_pair}

# ---- ages ----
age_src = {}
try:
    tm = json.load(open(os.path.join(RIP, "token_meta.json")))
    for p, v in tm.items():
        if v.get("pool_created_at"):
            age_src[p] = datetime.fromisoformat(v["pool_created_at"].replace("Z", "+00:00")).timestamp()
except Exception: tm = {}
for fn in ("_pair_created_cache.json", "_pair_created_cache2.json"):
    try:
        pc = json.load(open(os.path.join(RIP, fn)))
        for p, v in pc.items():
            if v: age_src.setdefault(p, float(v))
    except Exception: pass

# ---- sym map ----
sym = {}
for eps in led.values():
    for e in eps: sym.setdefault(e["pair"], e.get("sym"))

# ---- tape flows w/ maker for participation ----
tape = {}  # pair -> sorted list of (ep, kind, usd, maker)
for f in glob.glob(os.path.join(RIP, "live_tapes", "tape_*.jsonl")) + glob.glob(os.path.join(RIP, "tape_*.jsonl")):
    for line in open(f, encoding="utf-8"):
        try: t = json.loads(line)
        except Exception: continue
        if t["ts"] < "2026-07-01": continue
        tape.setdefault(t["pair"], []).append((iso2ep(t["ts"]), t["kind"], t["volume_usd"], t.get("maker")))
for p in tape: tape[p].sort()

def tape_win(p, t0, t1):
    tp = tape.get(p)
    if not tp: return None
    lo = bisect.bisect_left(tp, (t0, "", -1, None))
    hi = bisect.bisect_right(tp, (t1, "zz", 1e18, None))
    return tp[lo:hi]

# ---- bar helpers ----
def bidx(p, ep):
    ts = bar_ts.get(p)
    if not ts: return None
    i = bisect.bisect_right(ts, ep) - 1
    return i if i >= 0 else None

def vol_between(p, t0, t1):
    ts = bar_ts.get(p)
    if not ts: return None
    lo = bisect.bisect_left(ts, t0); hi = bisect.bisect_left(ts, t1)
    return sum(b[5] for b in bars_by_pair[p][lo:hi])

def px_at(p, ep, tol=1800):
    i = bidx(p, ep)
    if i is None or ep - bar_ts[p][i] > tol: return None
    return bars_by_pair[p][i][4]

def hi_before(p, ep):
    i = bidx(p, ep)
    if i is None: return None
    return max(b[2] for b in bars_by_pair[p][:i+1])

def feats(p, ep):
    """revival features at time ep for pair p"""
    bt = bar_ts.get(p)
    if not bt: return None
    cov_back_h = (ep - bt[0]) / 3600.0
    v_now = vol_between(p, ep - 3600, ep)              # entry hour vol
    v_prior24 = vol_between(p, ep - 25*3600, ep - 3600)  # prior 24h (excl entry hour)
    # peak hourly vol in coverage before entry-hour (dormancy reference)
    ts = bt; lo = 0; hi = bisect.bisect_left(ts, ep - 3600)
    hourly = collections.defaultdict(float)
    for b in bars_by_pair[p][lo:hi]:
        hourly[int(b[0] // 3600)] += b[5]
    peak_h = max(hourly.values()) if hourly else None
    px = px_at(p, ep)
    ath = hi_before(p, ep)
    below_ath = 100.0 * (px / ath - 1) if (px and ath) else None
    ramp = (v_now / (v_prior24 / 24.0)) if (v_now is not None and v_prior24) else None
    dorm = ((v_prior24 / 24.0) / peak_h) if (v_prior24 is not None and peak_h) else None
    # participation from tape: unique buyers entry hour vs prior 3h hourly avg
    tw = tape_win(p, ep - 3600, ep)
    ub_now = len({x[3] for x in tw if x[1] == "buy"}) if tw is not None else None
    tw3 = tape_win(p, ep - 4*3600, ep - 3600)
    ub_prior = (len({x[3] for x in tw3 if x[1] == "buy"}) / 3.0) if tw3 is not None else None
    return dict(cov_back_h=cov_back_h, v_now=v_now, v_prior24=v_prior24, peak_h=peak_h,
                dorm=dorm, ramp=ramp, px=px, below_ath=below_ath,
                ub_now=ub_now, ub_prior=ub_prior)

# ================= PART A: 07-03 winner >72h episodes =================
DAY = "2026-07-03"
winners = set(wbd[DAY]["winners"])
MIN_BUY = 20.0
rows = []
for w in winners:
    for e in led.get(w, []):
        if e["day"] != DAY or e["buy_usd"] < MIN_BUY or e.get("no_px"): continue
        p = e["pair"]
        if p not in age_src: continue
        ep0 = iso2ep(e["first_buy"])
        age_h = (ep0 - age_src[p]) / 3600.0
        if age_h <= 72: continue
        fx = feats(p, ep0) or {}
        rows.append(dict(w=w, pair=p, sym=sym.get(p), ep0=ep0, age_h=age_h,
                         buy_usd=e["buy_usd"], net=e["net"], ret=100*e["net"]/e["buy_usd"],
                         realized=e["realized"], frac_sold=e.get("frac_sold"),
                         first_buy=e["first_buy"], sell_px=e.get("sell_px"), buy_vwap=e.get("buy_vwap"),
                         n_buys=e["n_buys"], n_sells=e["n_sells"], **fx))

print("PART A: 07-03 winner >72h episodes: n_eps=%d n_tokens=%d n_wallets=%d" %
      (len(rows), len({r["pair"] for r in rows}), len({r["w"] for r in rows})))
tot_buy = sum(r["buy_usd"] for r in rows); tot_net = sum(r["net"] for r in rows)
print("ret on buy USD: %+.1f%% (buy $%.0f)" % (100*tot_net/tot_buy, tot_buy))

def dist(name, vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals: print("  %s: n=0" % name); return
    q = lambda f: vals[min(len(vals)-1, int(f*len(vals)))]
    print("  %s n=%d p10=%.2f p25=%.2f med=%.2f p75=%.2f p90=%.2f" %
          (name, len(vals), q(.1), q(.25), st.median(vals), q(.75), q(.9)))

# token-level (dedup): first winner entry per token, features there
by_tok = {}
for r in sorted(rows, key=lambda r: r["ep0"]):
    by_tok.setdefault(r["pair"], r)
toks = list(by_tok.values())
print("\nToken-level (first winner entry per token, n=%d):" % len(toks))
for f in ("age_h", "cov_back_h", "dorm", "ramp", "below_ath", "ub_now", "ub_prior", "v_now", "v_prior24", "peak_h"):
    dist(f, [t.get(f) for t in toks])

print("\nPer-token detail:")
for t in sorted(toks, key=lambda x: -sum(r["buy_usd"] for r in rows if r["pair"] == x["pair"])):
    n_eps = sum(1 for r in rows if r["pair"] == t["pair"])
    bu = sum(r["buy_usd"] for r in rows if r["pair"] == t["pair"])
    nn = sum(r["net"] for r in rows if r["pair"] == t["pair"])
    print(" %-12s age=%6.0fh eps=%3d buy=$%7.0f ret=%+6.1f%% | dorm=%s ramp=%s belowATH=%s ubNow=%s ubPr=%s vNow=%s covBack=%.0fh" %
          (((t["sym"] or t["pair"][:8]).encode("ascii", "replace").decode()), t["age_h"], n_eps, bu, 100*nn/bu if bu else 0,
           ("%.3f" % t["dorm"]) if t.get("dorm") is not None else "-",
           ("%.1f" % t["ramp"]) if t.get("ramp") is not None else "-",
           ("%.0f" % t["below_ath"]) if t.get("below_ath") is not None else "-",
           t.get("ub_now") if t.get("ub_now") is not None else "-",
           ("%.1f" % t["ub_prior"]) if t.get("ub_prior") is not None else "-",
           ("%.0f" % t["v_now"]) if t.get("v_now") is not None else "-",
           t["cov_back_h"]))

json.dump(rows, open(os.path.join(RIP, "_revival_eps_0703.json"), "w"), default=str)
print("\nage coverage: pairs_all=%d with_age=%d with_bars=%d" %
      (len(pairs_all), sum(1 for p in pairs_all if p in age_src), sum(1 for p in pairs_all if p in bars_by_pair)))
