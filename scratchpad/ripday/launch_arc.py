# launch_arc.py -- LAUNCH-ARC MINE (rerun, spec 2026-07-02)
# Hypothesis (AxiS): "nearly all newer tokens: massive run-up -> dump within
# first few hours -> steady recovery. Universal."
# Measures: (Q1) arc prevalence on an UNBIASED birth cohort (GT new_pools,
# captured at birth in _gt_newpools_cache.json), (Q2) trough discriminator
# recoverer-vs-corpse, (Q3) entry-timing tournament under the fleet exit
# stack (TP1 +6 half / TP2 +12 / stop -12 / 45m timestop), (Q4) age band.
# Spec arc: pump (peak>=2x baseline) -> dump (<=-40% from peak within 4h of
# peak) -> recovery (>= +20% bounce off running trough).
# Caches (additive, re-run cheap): _gt_newpools_cache.json, _gt_bars/,
# _ds_state_cache.json, _pair_created_cache.json.
# Usage: python scratchpad/ripday/launch_arc.py [--no-net]
# ASCII output only.

import json, glob, os, sys, time, bisect, statistics as st
from datetime import datetime, timezone

RIP = os.path.dirname(os.path.abspath(__file__))
NO_NET = "--no-net" in sys.argv
UA = {"User-Agent": "Mozilla/5.0 (research; launch-arc-study)"}
PACE_GT = 3.2
PACE_DS = 1.2

# ---- spec params ----
PUMP_MULT = 2.0          # pumped = early peak >= 2x baseline (first-bar open)
PEAK_WINDOW_H = 6.0      # early peak searched within first 6h of launch
DUMP_DD = -0.40          # dump = close <= peak*(1-0.40) within 4h OF PEAK
DUMP_WINDOW_H = 4.0
BOUNCE = 0.20            # recovery = close >= running_trough * 1.20
CORPSE_MIN_POST_H = 3.0  # need >=3h post-trough coverage to call corpse
TRACTION_MCAP = 100_000  # tradeable floor: peak mcap >= 100k
TRACTION_LIQ = 25_000    # ... and best-observed liq >= 25k (see caveat)
# exit stack (fleet badday_young_absorb)
TP1, TP2, STOP, TIMESTOP_S = 0.06, 0.12, -0.12, 45 * 60

def now_utc(): return datetime.now(timezone.utc).timestamp()

def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None

def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None

def fmt(x, pct=False, m=False):
    if x is None: return "  -  "
    if pct: return "%+.1f%%" % (100 * x)
    if m: return "%.0fm" % x
    return "%.2f" % x

def iso_to_epoch(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception: return None

def price_at(bars, t):
    ts = [b[0] for b in bars]
    i = bisect.bisect_right(ts, t) - 1
    return bars[i][4] if i >= 0 else None

# ================= net helpers =================

def gt_get(url, params=None):
    from curl_cffi import requests as cr
    for attempt in range(4):
        try:
            r = cr.get(url, params=params, headers=UA, impersonate="chrome", timeout=25)
            if r.status_code == 429:
                time.sleep(12 * (attempt + 1)); continue
            if r.status_code == 200: return r.json()
            return None
        except Exception:
            time.sleep(5)
    return None

def gt_new_pools_refresh(max_pages=10):
    """Additive: append current new_pools to cache (grows future cohorts)."""
    cache_f = os.path.join(RIP, "_gt_newpools_cache.json")
    cache = json.load(open(cache_f)) if os.path.exists(cache_f) else {"pools": {}, "fetched": []}
    if not NO_NET:
        added = 0
        for pg in range(1, max_pages + 1):
            j = gt_get("https://api.geckoterminal.com/api/v2/networks/solana/new_pools",
                       {"page": pg})
            time.sleep(PACE_GT)
            if not j or "data" not in j: break
            for d in j["data"]:
                a = d.get("attributes", {})
                addr = a.get("address")
                if not addr or addr in cache["pools"]: continue
                vol = a.get("volume_usd") or {}
                cache["pools"][addr] = {
                    "created": a.get("pool_created_at"),
                    "reserve": float(a.get("reserve_in_usd") or 0),
                    "vol_h1": float(vol.get("h1") or 0),
                    "vol_h24": float(vol.get("h24") or 0),
                    "name": a.get("name"), "seen": now_utc()}
                added += 1
        cache["fetched"].append(now_utc())
        json.dump(cache, open(cache_f, "w"))
        print("new_pools refresh: +%d pools (cache %d)" % (added, len(cache["pools"])))
    return cache["pools"]

def ds_pair_state(addrs):
    """DexScreener current state per pool addr. Cached (refetch if >2h old)."""
    cache_f = os.path.join(RIP, "_ds_state_cache.json")
    cache = json.load(open(cache_f)) if os.path.exists(cache_f) else {}
    nowt = now_utc()
    missing = [a for a in addrs
               if a not in cache or nowt - cache[a].get("_at", 0) > 7200]
    if missing and not NO_NET:
        import requests
        for i in range(0, len(missing), 30):
            batch = missing[i:i + 30]
            try:
                r = requests.get("https://api.dexscreener.com/latest/dex/pairs/solana/"
                                 + ",".join(batch), headers=UA, timeout=25)
                got = set()
                if r.status_code == 200:
                    for pr in (r.json() or {}).get("pairs") or []:
                        pa = pr.get("pairAddress")
                        if not pa: continue
                        got.add(pa)
                        liqd = pr.get("liquidity") or {}
                        cache[pa] = {
                            "_at": nowt,
                            "liq": float(liqd.get("usd") or 0),
                            "mcap": float(pr.get("marketCap") or 0),
                            "fdv": float(pr.get("fdv") or 0),
                            "price": float(pr.get("priceUsd") or 0),
                            "vol24": float((pr.get("volume") or {}).get("h24") or 0),
                            "token": (pr.get("baseToken") or {}).get("address"),
                            "sym": (pr.get("baseToken") or {}).get("symbol")}
                for a in batch:
                    if a not in got and a not in cache:
                        cache[a] = {"_at": nowt, "delisted": True}
            except Exception as e:
                print("  [ds] batch err:", e)
            time.sleep(PACE_DS)
        json.dump(cache, open(cache_f, "w"))
    return cache

def gt_minute_bars(pool_addr):
    bdir = os.path.join(RIP, "_gt_bars"); os.makedirs(bdir, exist_ok=True)
    f = os.path.join(bdir, pool_addr[:12] + ".json")
    if os.path.exists(f): return json.load(open(f))
    if NO_NET: return None
    j = gt_get("https://api.geckoterminal.com/api/v2/networks/solana/pools/%s/ohlcv/minute"
               % pool_addr, {"aggregate": 1, "limit": 1000, "currency": "usd"})
    time.sleep(PACE_GT)
    try: raw = j["data"]["attributes"]["ohlcv_list"]
    except Exception:
        json.dump([], open(f, "w")); return []
    bars = sorted([[int(b[0]), b[1], b[2], b[3], b[4], float(b[5])] for b in raw],
                  key=lambda b: b[0])
    json.dump(bars, open(f, "w"))
    return bars

# ================= arc classification =================

def classify_arc(bars, launch_ts, cover_min=90):
    """Spec classes: never_pumped | up_only | slow_fade |
    arc_recovered | arc_corpse | arc_censored. Plus geometry."""
    if not bars or len(bars) < 5: return {"excl": "thin_bars"}
    if launch_ts is None: return {"excl": "no_launch_ts"}
    if (bars[0][0] - launch_ts) / 60.0 > cover_min:
        return {"excl": "late_coverage"}   # missed the launch window
    base = bars[0][1] or bars[0][4]
    if base <= 0: return {"excl": "bad_base"}
    win_end = launch_ts + PEAK_WINDOW_H * 3600
    early = [b for b in bars if b[0] <= win_end]
    if len(early) < 5: return {"excl": "thin_early"}
    pk = max(early, key=lambda b: b[4])
    peak_ts, peak_px = pk[0], pk[4]
    pump_x = peak_px / base
    out = {"excl": None, "launch_ts": launch_ts, "peak_ts": peak_ts,
           "peak_px": peak_px, "pump_x": pump_x,
           "t_launch_peak_m": (peak_ts - launch_ts) / 60.0}
    post_peak = [b for b in bars if b[0] > peak_ts]
    max_dd_all = min((b[4] / peak_px - 1 for b in post_peak), default=0.0)
    out["max_dd_from_peak"] = max_dd_all
    if pump_x < PUMP_MULT:
        out["cls"] = "never_pumped"; return out
    # dump trigger: first close <= peak*(1-40%) within 4h OF PEAK
    trig = None
    for b in post_peak:
        if b[0] > peak_ts + DUMP_WINDOW_H * 3600: break
        if b[4] <= peak_px * (1 + DUMP_DD):
            trig = b; break
    if trig is None:
        out["cls"] = "slow_fade" if max_dd_all <= DUMP_DD else "up_only"
        return out
    out["trig_ts"], out["trig_px"] = trig[0], trig[4]
    # walk: running trough; recovery = close >= trough * (1+BOUNCE)
    run_min, run_min_ts = trig[4], trig[0]
    recov_ts = None
    trough_px, trough_ts = trig[4], trig[0]
    for b in bars:
        if b[0] <= trig[0]: continue
        if b[4] < run_min: run_min, run_min_ts = b[4], b[0]
        # recovery print must be a real trade (>= $100 bar vol), not dust
        if b[4] >= run_min * (1 + BOUNCE) and b[5] >= 100:
            recov_ts = b[0]; trough_px, trough_ts = run_min, run_min_ts
            break
    if recov_ts is None:
        post = [b for b in bars if b[0] >= trig[0]]
        gb = min(post, key=lambda b: b[4])
        trough_px, trough_ts = gb[4], gb[0]
    post_trough_h = (bars[-1][0] - trough_ts) / 3600.0
    out.update({"trough_ts": trough_ts, "trough_px": trough_px,
                "recov_ts": recov_ts, "dd": trough_px / peak_px - 1,
                "t_peak_trough_m": (trough_ts - peak_ts) / 60.0,
                "t_trough_recov_m": ((recov_ts - trough_ts) / 60.0) if recov_ts else None,
                "post_trough_h": post_trough_h,
                "terminal": bars[-1][4] / peak_px - 1})
    if out["dd"] <= -0.95:
        out["cls"] = "rugged"   # dust low; any +20% print is unrealizable
    elif recov_ts is not None: out["cls"] = "arc_recovered"
    elif post_trough_h >= CORPSE_MIN_POST_H: out["cls"] = "arc_corpse"
    else: out["cls"] = "arc_censored"
    return out

# ================= Q2: trough features =================

def trough_features(bars, arc):
    pk, tr = arc["peak_ts"], arc["trough_ts"]
    def vsum(a, b_): return sum(x[5] for x in bars if a <= x[0] < b_)
    def nb(a, b_): return sum(1 for x in bars if a <= x[0] < b_)
    def green_share(a, b_):
        seg = [x for x in bars if a <= x[0] < b_]
        tot = sum(x[5] for x in seg)
        if tot <= 0: return None
        return sum(x[5] for x in seg if x[4] >= x[1]) / tot
    vol_peak15 = vsum(pk, pk + 900)
    vol_tr15 = vsum(tr, tr + 900)
    f = {"dd": arc["dd"], "min_since_peak": arc["t_peak_trough_m"],
         "vol_tr15": vol_tr15, "vol_peak15": vol_peak15,
         "vol_ratio": (vol_tr15 / vol_peak15) if vol_peak15 > 0 else None,
         "green_share_tr15": green_share(tr, tr + 900),
         "bars_tr15": nb(tr, tr + 900)}
    return f

# ================= Q3: entry rules + exit stack =================

def rolling_pch1(bars, i, launch_ts):
    """pc vs 60m ago (LOCF); if <60m old, vs first bar open."""
    t = bars[i][0]
    ref = price_at(bars, t - 3600)
    if ref is None: ref = bars[0][1] or bars[0][4]
    if not ref: return None
    return bars[i][4] / ref - 1

def entry_knife(bars, arc):
    """(a) first bar after peak with rolling pc_h1 <= -30%."""
    for i, b in enumerate(bars):
        if b[0] <= arc["peak_ts"]: continue
        if b[0] > arc["peak_ts"] + 8 * 3600: break
        pc = rolling_pch1(bars, i, arc["launch_ts"])
        if pc is not None and pc <= -0.30:
            return (b[0], b[4])
    return None

def entry_higher_low(bars, arc):
    """(b) after dump underway (dd<=-30% from peak): running-min low that
    holds 10+ min with no lower low -> enter at the bar completing 10min."""
    started = False
    run_low, run_low_ts = None, None
    for b in bars:
        if b[0] <= arc["peak_ts"]: continue
        if not started:
            if b[4] / arc["peak_px"] - 1 <= -0.30: started = True
            else: continue
        if run_low is None or b[3] < run_low:
            run_low, run_low_ts = b[3], b[0]
            continue
        if b[0] - run_low_ts >= 600 and b[4] > run_low:
            return (b[0], b[4])
    return None

def entry_plus30(bars, arc):
    """(c) trough held 30 min (no lower low since running min) -> enter."""
    started = False
    run_low, run_low_ts = None, None
    for b in bars:
        if b[0] <= arc["peak_ts"]: continue
        if not started:
            if b[4] / arc["peak_px"] - 1 <= -0.30: started = True
            else: continue
        if run_low is None or b[3] < run_low:
            run_low, run_low_ts = b[3], b[0]
            continue
        if b[0] - run_low_ts >= 1800:
            return (b[0], b[4])
    return None

def sim_exit_stack(bars, ts_e, px_e):
    """TP1 +6 sell half, TP2 +12 rest, stop -12 (pessimistic: same-bar stop
    wins), 45m timestop. Returns (blended fractional pnl, outcome) or None
    if censored (bars end before resolution)."""
    tp1, tp2, stp = px_e * (1 + TP1), px_e * (1 + TP2), px_e * (1 + STOP)
    tend = ts_e + TIMESTOP_S
    pos, pnl, hit1 = 1.0, 0.0, False
    if bars[-1][0] < tend: return None
    for b in bars:
        if b[0] <= ts_e: continue
        if b[0] > tend: break
        lo, hi = b[3], b[2]
        if lo <= stp:
            return (pnl + pos * STOP, "stop_after_tp1" if hit1 else "stop")
        if not hit1 and hi >= tp1:
            pnl += 0.5 * TP1; pos = 0.5; hit1 = True
        if hit1 and hi >= tp2:
            return (pnl + pos * TP2, "tp2")
    px_end = price_at(bars, tend)
    return (pnl + pos * (px_end / px_e - 1), "timestop")

# ================= reporting =================

def prevalence(rows, title):
    scored = [r for r in rows if r.get("excl") is None]
    from collections import Counter
    c = Counter(r["cls"] for r in scored)
    print("\n== ARC PREVALENCE: %s ==" % title)
    print("scored: %d (excluded: %s)" % (len(scored),
          dict(Counter(r["excl"] for r in rows if r.get("excl")))))
    if not scored: return
    for k in ("arc_recovered", "arc_corpse", "arc_censored", "rugged",
              "up_only", "slow_fade", "never_pumped"):
        n = c.get(k, 0)
        print("  %-14s %3d  (%.0f%%)" % (k, n, 100.0 * n / len(scored)))
    arcs = [r for r in scored if r["cls"].startswith("arc") or r["cls"] == "rugged"]
    res = [r for r in arcs if r["cls"] != "arc_censored"]
    rec = [r for r in arcs if r["cls"] == "arc_recovered"]
    if res:
        print("  pump->dump arc rate: %d/%d = %.0f%% ; recovery rate among "
              "resolved arcs: %d/%d = %.0f%%"
              % (len(arcs), len(scored), 100.0 * len(arcs) / len(scored),
                 len(rec), len(res), 100.0 * len(rec) / len(res)))
    if arcs:
        print("  med timings: launch->peak %s | peak->trough %s | trough->+20%% %s"
              % (fmt(med([r["t_launch_peak_m"] for r in arcs]), m=True),
                 fmt(med([r["t_peak_trough_m"] for r in arcs]), m=True),
                 fmt(med([r["t_trough_recov_m"] for r in rec]), m=True)))
        print("  med dd@trough: rec %s | corpse %s ; terminal-vs-peak: rec %s | corpse %s"
              % (fmt(med([r["dd"] for r in rec]), pct=True),
                 fmt(med([r["dd"] for r in arcs if r["cls"] == "arc_corpse"]), pct=True),
                 fmt(med([r["terminal"] for r in rec]), pct=True),
                 fmt(med([r["terminal"] for r in arcs if r["cls"] == "arc_corpse"]), pct=True)))

def run_tournament(arc_rows, title):
    print("\n== ENTRY TOURNAMENT (%s) — exit stack TP1+6/TP2+12/stop-12/45m ==" % title)
    rules = {"knife_pch1-30": entry_knife, "higher_low_10m": entry_higher_low,
             "trough_hold_30m": entry_plus30}
    hdr = "%-16s %4s %5s %7s %8s %8s %6s %6s %7s"
    print(hdr % ("rule", "n", "fill", "age@e", "mean", "median", "win%", "stop%", "worst"))
    table = {}
    for name, fn in rules.items():
        outs, ages, res_rows = [], [], []
        n_arc = 0
        for r in arc_rows:
            n_arc += 1
            e = fn(r["bars"], r)
            if e is None: continue
            ts_e, px_e = e
            sim = sim_exit_stack(r["bars"], ts_e, px_e)
            age_h = (ts_e - r["launch_ts"]) / 3600.0
            if sim is None: continue
            pnl, outc = sim
            outs.append((pnl, outc)); ages.append(age_h)
            res_rows.append({"pair": r["pair"], "pnl": pnl, "outc": outc,
                             "age_h": age_h, "cls": r["cls"]})
        pnls = [p for p, _ in outs]
        stops = sum(1 for _, o in outs if o.startswith("stop"))
        print(hdr % (name, len(pnls),
                     "%.0f%%" % (100.0 * len(pnls) / n_arc) if n_arc else "-",
                     fmt(med(ages), m=False) + "h",
                     fmt(mean(pnls), pct=True), fmt(med(pnls), pct=True),
                     "%.0f%%" % (100.0 * sum(1 for p in pnls if p > 0) / len(pnls)) if pnls else "-",
                     "%.0f%%" % (100.0 * stops / len(pnls)) if pnls else "-",
                     fmt(min(pnls) if pnls else None, pct=True)))
        table[name] = res_rows
    return table

def age_band_report(table):
    print("\n== Q4 AGE BAND (entry age, all rules pooled and per rule) ==")
    hdr = "%-16s %-8s %4s %8s %6s"
    print(hdr % ("rule", "band", "n", "mean", "win%"))
    for name, rows in table.items():
        for lo, hi, lab in ((0, 2, "<2h"), (2, 8, "2-8h"), (8, 99, ">8h")):
            sel = [r for r in rows if lo <= r["age_h"] < hi]
            if not sel:
                print(hdr % (name, lab, 0, "-", "-")); continue
            pn = [r["pnl"] for r in sel]
            print(hdr % (name, lab, len(sel), fmt(mean(pn), pct=True),
                         "%.0f%%" % (100.0 * sum(1 for p in pn if p > 0) / len(pn))))

def discriminator_report(arc_rows, title):
    print("\n== Q2 TROUGH DISCRIMINATOR (%s) ==" % title)
    feats = []
    for r in arc_rows:
        if r["cls"] not in ("arc_recovered", "arc_corpse"): continue
        f = trough_features(r["bars"], r)
        f["cls"] = r["cls"]; feats.append(f)
    rec = [f for f in feats if f["cls"] == "arc_recovered"]
    cor = [f for f in feats if f["cls"] == "arc_corpse"]
    print("n: recoverer %d, corpse %d" % (len(rec), len(cor)))
    keys = ["dd", "min_since_peak", "vol_tr15", "vol_peak15", "vol_ratio",
            "green_share_tr15", "bars_tr15"]
    print("%-18s %10s %10s" % ("feature (median)", "recoverer", "corpse"))
    for k in keys:
        print("%-18s %10s %10s" % (k, fmt(med([f.get(k) for f in rec])),
                                   fmt(med([f.get(k) for f in cor]))))
    print("threshold sweep -> recov-precision | recall | corpses passed:")
    def sweep(key, thrs, ge=True):
        base = [f for f in feats if f.get(key) is not None]
        br = [f for f in base if f["cls"] == "arc_recovered"]
        for t in thrs:
            sel = [f for f in base if (f[key] >= t if ge else f[key] <= t)]
            if not sel: continue
            pr = sum(1 for f in sel if f["cls"] == "arc_recovered")
            print("  %s %s %-7s -> prec %.0f%% (%d/%d) | recall %.0f%% | corpses %d/%d"
                  % (key, ">=" if ge else "<=", t, 100.0 * pr / len(sel), pr,
                     len(sel), (100.0 * pr / len(br)) if br else 0,
                     len(sel) - pr, len(base) - len(br)))
    sweep("vol_ratio", [0.05, 0.10, 0.25])
    sweep("green_share_tr15", [0.4, 0.5, 0.6])
    sweep("bars_tr15", [8, 12])
    sweep("dd", [-0.85, -0.75], ge=False)
    sweep("min_since_peak", [30, 60], ge=False)
    return feats

# ================= main =================

def main():
    print("launch_arc.py run %s UTC" %
          datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    nowt = now_utc()

    # ---------- Part B first (the headline): unbiased birth cohort ----------
    pools = gt_new_pools_refresh()
    cohort = []
    for addr, p in pools.items():
        c = iso_to_epoch(p.get("created"))
        if c is None: continue
        age_h = (nowt - c) / 3600.0
        if age_h < 6.0 or age_h > 30.0: continue   # need lifecycle room + bar reach
        q = dict(p); q["addr"] = addr; q["created_ep"] = c; q["age_h"] = age_h
        cohort.append(q)
    print("\nbirth cohort (age 6-30h now): %d pools" % len(cohort))

    ds = ds_pair_state([p["addr"] for p in cohort])
    # screen: fetch bars only where any sign of life (DS vol24 covers the
    # whole life for a <24h pool, so a dead-now-but-pumped pool still shows)
    need_bars, dead = [], []
    for p in cohort:
        s = ds.get(p["addr"], {})
        alive = (s.get("vol24", 0) >= 2000 or s.get("liq", 0) >= 10000
                 or p["reserve"] >= 25000 or p["vol_h1"] >= 2000)
        (need_bars if alive else dead).append(p)
    print("screen: %d fetch-bars candidates, %d no-life (skipped as no-traction)"
          % (len(need_bars), len(dead)))

    gt_rows = []
    for p in need_bars:
        bars = gt_minute_bars(p["addr"])
        s = ds.get(p["addr"], {})
        row = {"pair": p["addr"], "name": p.get("name"), "created_ep": p["created_ep"],
               "ds": s}
        if not bars:
            row["excl"] = "no_bars"; gt_rows.append(row); continue
        # traction: reconstruct peak mcap via DS supply (mcap/price), fallback fdv
        supply = None
        if s.get("price") and s.get("mcap"): supply = s["mcap"] / s["price"]
        elif s.get("price") and s.get("fdv"): supply = s["fdv"] / s["price"]
        peak_px = max(b[4] for b in bars)
        peak_mcap = supply * peak_px if supply else None
        liq_best = max(p.get("reserve", 0), s.get("liq", 0))
        row["peak_mcap"] = peak_mcap; row["liq_best"] = liq_best
        row["traction"] = bool(peak_mcap and peak_mcap >= TRACTION_MCAP
                               and liq_best >= TRACTION_LIQ)
        row["traction_mcap_only"] = bool(peak_mcap and peak_mcap >= TRACTION_MCAP)
        row.update(classify_arc(bars, p["created_ep"]))
        row["bars"] = bars
        gt_rows.append(row)

    n_all = len(cohort)
    n_traction = sum(1 for r in gt_rows if r.get("traction"))
    n_mcap_only = sum(1 for r in gt_rows if r.get("traction_mcap_only"))
    print("\nNO-ARC / NO-TRACTION SHARE (full birth cohort n=%d):" % n_all)
    print("  reached tradeable floor (peak mcap>=100k AND best-liq>=25k): %d (%.0f%%)"
          % (n_traction, 100.0 * n_traction / n_all if n_all else 0))
    print("  peak mcap>=100k regardless of liq: %d (%.0f%%)"
          % (n_mcap_only, 100.0 * n_mcap_only / n_all if n_all else 0))
    print("  never any traction: %d (%.0f%%)"
          % (n_all - n_mcap_only, 100.0 * (n_all - n_mcap_only) / n_all if n_all else 0))
    print("  [caveat: liq history unavailable; liq_best = max(birth reserve, "
          "current DS liq) -> traction slightly undercounted]")

    scored_all = [r for r in gt_rows if r.get("excl") is None]
    n_supply_unknown = sum(1 for r in gt_rows if r.get("peak_mcap") is None)
    n_50k = sum(1 for r in gt_rows if r.get("peak_mcap") and r["peak_mcap"] >= 50000)
    print("  sensitivity: peak mcap>=50k: %d (%.0f%% of cohort) ; supply-unknown "
          "(counted no-traction): %d" % (n_50k, 100.0 * n_50k / n_all, n_supply_unknown))
    tr_rows = [r for r in gt_rows if r.get("traction") and r.get("excl") is None]
    tr_rows_m = [r for r in gt_rows if r.get("traction_mcap_only") and r.get("excl") is None]
    prevalence(scored_all, "GT unbiased, ANY-LIFE cohort (bar-fetched, no floor)")
    prevalence(tr_rows_m, "GT unbiased, peak-mcap>=100k (incl. liq<25k)")
    prevalence(tr_rows, "GT unbiased, FULL tradeable floor (mcap>=100k & liq>=25k)")

    gt_arcs = [r for r in tr_rows_m if r.get("cls", "").startswith("arc")]
    gt_arcs_any = [r for r in scored_all if r.get("cls", "").startswith("arc")]
    discriminator_report(gt_arcs_any, "GT unbiased ANY-LIFE arcs (traction n too thin)")
    table = run_tournament(gt_arcs, "GT traction cohort (mcap>=100k) [THIN]")
    table_any = run_tournament(gt_arcs_any, "GT ANY-LIFE arcs (incl. sub-floor)")
    age_band_report(table_any)

    # ---------- Part A: local runner-biased set (supplementary) ----------
    print("\n---------- SUPPLEMENTARY: local recorded 172-pair set (runner-biased) ----------")
    local = {}
    for f in sorted(glob.glob(os.path.join(RIP, "ohlc2_*.json"))):
        d = json.load(open(f))
        local[d["pair"]] = sorted(d["bars"], key=lambda b: b[0])
    tokmap = {}
    for f in glob.glob(os.path.join(RIP, "tape_*.jsonl")):
        try:
            with open(f, encoding="utf-8") as fh:
                j = json.loads(fh.readline())
            tokmap[j["pair"]] = (j.get("token"), j.get("sym"))
        except Exception: pass
    created_f = os.path.join(RIP, "_pair_created_cache.json")
    created = json.load(open(created_f)) if os.path.exists(created_f) else {}
    loc_rows = []
    for pair, bars in local.items():
        r = {"pair": pair, "sym": tokmap.get(pair, (None, "?"))[1]}
        launch = created.get(pair)
        if launch is None: r["excl"] = "no_launch_ts"
        else:
            gap_m = (bars[0][0] - launch) / 60.0
            r["tier"] = "strict" if gap_m <= 90 else "late"
            r.update(classify_arc(bars, launch, cover_min=180)); r["bars"] = bars
        loc_rows.append(r)
    scored = [r for r in loc_rows if r.get("excl") is None]
    prevalence(loc_rows, "local runner-biased (coverage from launch only)")
    loc_arcs = [r for r in scored if r.get("cls", "").startswith("arc")]
    if loc_arcs:
        discriminator_report(loc_arcs, "local runner-biased")
        t2 = run_tournament(loc_arcs, "local runner-biased")
        age_band_report(t2)

    # persist
    out = {"run_at": nowt,
           "gt": [{k: v for k, v in r.items() if k not in ("bars",)} for r in gt_rows],
           "cohort_n": n_all, "traction_n": n_traction,
           "local": [{k: v for k, v in r.items() if k != "bars"} for r in loc_rows]}
    json.dump(out, open(os.path.join(RIP, "_launch_arc_results.json"), "w"))
    print("\nsaved: _launch_arc_results.json")

if __name__ == "__main__":
    main()
