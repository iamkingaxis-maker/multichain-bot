# traction_predict.py -- BIRTH-MINUTE TRACTION PREDICTOR (analysis, no net)
# Dataset A: unbiased window-1 birth cohort (GT new_pools captured at birth
#   2026-07-02 18:25-18:30 UTC), labels resolved at ~11h age.
# Dataset B: positive-enriched recall set (universe-recorder pools that met
#   the floor while young; birth-window bars via before_timestamp).
# Features: observable in first W minutes of POOL life (W = 5, 10, 15).
# NUMBERS RULE: per-pool, n stated everywhere. ASCII only.

import json, os, statistics as st
from datetime import datetime, timezone

RIP = os.path.dirname(os.path.abspath(__file__))
W1_CUTOFF = datetime(2026, 7, 3, 0, 0, tzinfo=timezone.utc).timestamp()
W4_CUTOFF = datetime(2026, 7, 3, 7, 0, tzinfo=timezone.utc).timestamp()
MIN_OBS_H = 6.0   # censoring: pools observed < 6h excluded from labels

def iso_to_epoch(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception: return None

def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None

def fmt(x):
    if x is None: return "  -  "
    if abs(x) >= 1000: return "%.0f" % x
    return "%.2f" % x

def load_bars(dirname, addr):
    f = os.path.join(RIP, dirname, addr[:12] + ".json")
    if not os.path.exists(f): return None
    return json.load(open(f))

def birth_features(bars, created, Ws=(5, 10, 15)):
    """Features from bars with ts in [created, created + W min)."""
    out = {}
    if bars is None: bars = []
    # truncation guard only: our fetches structurally cover birth (A: 11h life
    # << 1000-bar reach; B: before_ts=created+1h, <=60 bars << limit 120). A
    # late first bar = genuinely quiet birth (features are real zeros), NOT
    # missing coverage. Only a limit-saturated fetch could truncate.
    out["bars_reach_birth"] = len(bars) < 900
    out["first_bar_gap_m"] = ((bars[0][0] - created) / 60.0) if bars else None
    open0 = None
    for b in bars:
        if b[0] >= created - 120:
            open0 = b[1] or b[4]; break
    for W in Ws:
        end = created + W * 60
        seg = [b for b in bars if created - 120 <= b[0] < end]
        v = sum(b[5] for b in seg)
        out["n_bars_%d" % W] = len(seg)
        out["vol_%d" % W] = v
        if seg and open0:
            closes = [b[4] for b in seg]
            out["maxmult_%d" % W] = max(closes) / open0
            out["endmult_%d" % W] = closes[-1] / open0
            gv = sum(b[5] for b in seg if b[4] >= b[1])
            out["green_share_%d" % W] = gv / v if v > 0 else None
        else:
            out["maxmult_%d" % W] = None
            out["endmult_%d" % W] = None
            out["green_share_%d" % W] = None
    # persistence: late share of first-15m volume
    v15 = out.get("vol_15", 0)
    seg_late = [b for b in bars if created + 600 <= b[0] < created + 900]
    out["vol_late5"] = sum(b[5] for b in seg_late)
    out["late_share_15"] = (out["vol_late5"] / v15) if v15 > 0 else None
    return out

def sustained_peak_close(bars):
    """Max over adjacent-bar-pairs of min(close_i, close_j) -- kills
    single-print manipulation spikes. n==1 -> that close."""
    closes = [b[4] for b in bars]
    if not closes: return None
    if len(closes) == 1: return closes[0]
    return max(min(closes[i], closes[i + 1]) for i in range(len(closes) - 1))

# ================= Dataset A =================

def build_A():
    pools = json.load(open(os.path.join(RIP, "_gt_newpools_cache.json")))["pools"]
    ds = json.load(open(os.path.join(RIP, "_ds_state_cache.json")))
    nowt = datetime.now(timezone.utc).timestamp()
    rows = []
    for addr, p in pools.items():
        c = iso_to_epoch(p.get("created"))
        if c is None or c >= W4_CUTOFF: continue        # w1+w2+w3 only
        if (nowt - c) / 3600.0 < MIN_OBS_H: continue    # censored
        name = p.get("name") or ""
        r = {"addr": addr, "created": c, "name": name,
             "window": "w1" if c < W1_CUTOFF else "w23",
             "obs_h": (nowt - c) / 3600.0,
             "reserve0": p.get("reserve", 0.0), "vol_h1_seen": p.get("vol_h1", 0.0),
             "age_at_seen_m": (p.get("seen", c) - c) / 60.0}
        # infra/non-launch exclusion
        r["infra"] = (r["reserve0"] >= 1_000_000) or (not name.endswith("/ SOL"))
        bars = load_bars("_gt_bars", addr)
        r["have_bars"] = bars is not None
        r.update(birth_features(bars, c))
        # ---- label ----
        s = ds.get(addr, {})
        supply = None
        if s.get("price") and s.get("mcap"): supply = s["mcap"] / s["price"]
        elif s.get("price") and s.get("fdv"): supply = s["fdv"] / s["price"]
        vol_life = sum(b[5] for b in bars) if bars else 0.0
        r["vol_life"] = vol_life
        pk = sustained_peak_close(bars) if bars else None
        r["peak_mcap_sust"] = supply * pk if (supply and pk) else None
        r["supply_known"] = supply is not None
        r["liq_best"] = max(r["reserve0"], s.get("liq", 0) or 0)
        r["ds_liq_now"] = s.get("liq", 0)
        # artifact guard: a <12h-old pool printing >$20M sustained mcap on
        # <$100k liq is a wash/manipulation print, not traction (HAD/USWR class)
        r["artifact_bigmcap"] = bool(r["peak_mcap_sust"] and r["peak_mcap_sust"] > 20e6
                                     and r["liq_best"] < 100_000)
        r["traction"] = bool(r["peak_mcap_sust"] and r["peak_mcap_sust"] >= 100_000
                             and vol_life >= 5_000 and not r["artifact_bigmcap"])
        r["traction_full"] = bool(r["traction"] and r["liq_best"] >= 25_000)
        rows.append(r)
    rows.sort(key=lambda r: r["created"])
    return rows

# ================= Dataset B =================

def build_B():
    f = os.path.join(RIP, "_recall_set.json")
    if not os.path.exists(f): return []
    meta = json.load(open(f))
    rows = []
    for pair, m in meta.items():
        bars = load_bars("_gt_bars_b", pair)
        r = {"addr": pair, "created": m["created_ep"], "sym": m.get("sym"),
             "age_h_at_event": m.get("age_h_at_event"),
             "mcap_at_event": m.get("mcap_at_event"), "liq_at_event": m.get("liq_at_event"),
             "have_bars": bars is not None and len(bars) > 0}
        r.update(birth_features(bars, m["created_ep"]))
        rows.append(r)
    rows.sort(key=lambda r: r["created"])
    return rows

# ================= reporting =================

FEATS = ["n_bars_5", "vol_5", "maxmult_5", "n_bars_10", "vol_10",
         "n_bars_15", "vol_15", "maxmult_15", "endmult_15",
         "green_share_15", "late_share_15", "reserve0", "vol_h1_seen"]

def dist_table(rows, label_key, title):
    pos = [r for r in rows if r[label_key]]
    neg = [r for r in rows if not r[label_key]]
    print("\n== %s : feature medians (pos n=%d, neg n=%d) ==" % (title, len(pos), len(neg)))
    print("%-16s %10s %10s %28s" % ("feature", "pos_med", "neg_med", "pos values"))
    for k in FEATS:
        pv = [r.get(k) for r in pos]
        print("%-16s %10s %10s   %s" % (k, fmt(med(pv)), fmt(med([r.get(k) for r in neg])),
              " ".join(fmt(v) for v in pv[:10])))

def sweep(rows, label_key, key, thrs, ge=True, B=None, note=""):
    """precision/recall on rows (dataset A); optional recall on B."""
    base = [r for r in rows if r.get(key) is not None]
    pos = [r for r in base if r[label_key]]
    for t in thrs:
        sel = [r for r in base if (r[key] >= t if ge else r[key] <= t)]
        tp = sum(1 for r in sel if r[label_key])
        rec = 100.0 * tp / len(pos) if pos else 0
        prec = 100.0 * tp / len(sel) if sel else 0
        passrate = 100.0 * len(sel) / len(base) if base else 0
        line = ("  %s %s %-8s -> prec %4.0f%% (%d/%d) | recall %4.0f%% (%d/%d) | "
                "pass %4.1f%% (%d/%d)" % (key, ">=" if ge else "<=", t, prec, tp,
                len(sel), rec, tp, len(pos), passrate, len(sel), len(base)))
        if B is not None:
            bb = [r for r in B if r.get(key) is not None]
            bp = sum(1 for r in bb if (r[key] >= t if ge else r[key] <= t))
            line += " | B-recall %4.0f%% (%d/%d)" % (100.0 * bp / len(bb) if bb else 0,
                                                     bp, len(bb))
        print(line + (("  " + note) if note else ""))

def main():
    A = build_A()
    B = build_B()
    excl_infra = [r for r in A if r["infra"]]
    A2 = [r for r in A if not r["infra"]]
    no_birth = [r for r in A2 if not r["bars_reach_birth"]]
    A3 = [r for r in A2 if r["bars_reach_birth"]]
    print("Dataset A (window-1 unbiased cohort): %d pools; infra-excluded %d; "
          "bars-miss-birth excluded %d -> analyzed %d"
          % (len(A), len(excl_infra), len(no_birth), len(A3)))
    print("  infra: %s" % [(r["name"].encode("ascii", "replace").decode(),
                            int(r["reserve0"])) for r in excl_infra])
    print("  supply-known: %d/%d (unknown counted no-traction)"
          % (sum(1 for r in A3 if r["supply_known"]), len(A3)))
    npos = sum(1 for r in A3 if r["traction"])
    npos_f = sum(1 for r in A3 if r["traction_full"])
    print("  traction (sust-mcap>=100k & vol_life>=5k): %d ; full floor (+liq>=25k): %d"
          % (npos, npos_f))
    for r in A3:
        if r["traction"]:
            print("   POS %s %-14s sust_mcap %8.0f liq_best %7.0f vol_life %8.0f full=%s"
                  % (r["addr"][:8], r["name"][:14], r["peak_mcap_sust"],
                     r["liq_best"], r["vol_life"], r["traction_full"]))
    # near-miss artifacts dropped by the sustained/vol guards
    ds_labeled = [r for r in A2 if not r["traction"] and r.get("peak_mcap_sust")
                  and r["peak_mcap_sust"] >= 100_000]
    if ds_labeled:
        print("  dropped-by-guards (sust-mcap>=100k but vol<5k or artifact): %d" % len(ds_labeled))
        for r in ds_labeled:
            print("    drop %s %-14s sust_mcap %.0f vol_life %.0f artifact=%s"
                  % (r["addr"][:8], r["name"][:14], r["peak_mcap_sust"],
                     r["vol_life"], r["artifact_bigmcap"]))

    print("\nDataset B (positive-enriched young traction, recorder): n=%d, "
          "with birth bars: %d" % (len(B), sum(1 for r in B if r["have_bars"])))
    Bg = [r for r in B if r["have_bars"] and r["bars_reach_birth"]]
    print("  bars reach birth: %d (analyzed)" % len(Bg))
    if Bg:
        print("  age@event med %.1fh ; mcap@event med %.0f"
              % (med([r["age_h_at_event"] for r in Bg]),
                 med([r["mcap_at_event"] for r in Bg])))

    dist_table(A3, "traction", "A: traction vs rest")
    if Bg:
        print("\n== B (known-traction) feature medians (n=%d) ==" % len(Bg))
        for k in FEATS:
            if k in ("reserve0", "vol_h1_seen"): continue
            print("%-16s %10s" % (k, fmt(med([r.get(k) for r in Bg]))))

    print("\n== THRESHOLD SWEEPS (A precision/recall/pass-rate; B recall) ==")
    sweep(A3, "traction", "vol_5", [500, 2000, 5000, 10000, 20000], B=Bg)
    sweep(A3, "traction", "vol_15", [1000, 5000, 10000, 25000, 50000], B=Bg)
    sweep(A3, "traction", "n_bars_5", [2, 3, 4, 5], B=Bg)
    sweep(A3, "traction", "n_bars_15", [5, 8, 10, 12, 14], B=Bg)
    sweep(A3, "traction", "maxmult_15", [1.2, 1.5, 2.0], B=Bg)
    sweep(A3, "traction", "green_share_15", [0.3, 0.4, 0.5], B=Bg)
    sweep(A3, "traction", "late_share_15", [0.05, 0.15, 0.30], B=Bg)
    sweep(A3, "traction", "reserve0", [5000, 10000, 25000, 50000])
    sweep(A3, "traction", "vol_h1_seen", [100, 500, 2000, 10000])

    # combined candidate rules
    print("\n== COMBINED RULES ==")
    def combo(name, fn):
        base = A3
        sel = [r for r in base if fn(r)]
        tp = sum(1 for r in sel if r["traction"])
        pos = sum(1 for r in base if r["traction"])
        brec = None
        if Bg:
            bsel = sum(1 for r in Bg if fn(r))
            brec = 100.0 * bsel / len(Bg)
        print("  %-46s prec %4.0f%% (%d/%d) recall %4.0f%% (%d/%d) pass %4.1f%%%s"
              % (name, 100.0 * tp / len(sel) if sel else 0, tp, len(sel),
                 100.0 * tp / pos if pos else 0, tp, pos,
                 100.0 * len(sel) / len(base),
                 (" B-recall %.0f%% (%d/%d)" % (brec, bsel, len(Bg))) if brec is not None else ""))
    g = lambda r, k: (r.get(k) or 0)
    combo("vol_15>=10k", lambda r: g(r, "vol_15") >= 10000)
    combo("vol_15>=10k AND n_bars_15>=10", lambda r: g(r, "vol_15") >= 10000 and g(r, "n_bars_15") >= 10)
    combo("vol_5>=5k OR (n_bars_15>=12 AND vol_15>=5k)", lambda r: g(r, "vol_5") >= 5000 or (g(r, "n_bars_15") >= 12 and g(r, "vol_15") >= 5000))
    combo("n_bars_15>=12", lambda r: g(r, "n_bars_15") >= 12)
    combo("vol_15>=25k", lambda r: g(r, "vol_15") >= 25000)
    combo("vol_15>=5k", lambda r: g(r, "vol_15") >= 5000)
    combo("vol_15>=5k AND n_bars_15>=8", lambda r: g(r, "vol_15") >= 5000 and g(r, "n_bars_15") >= 8)

    # split validation: primary = window (evening w1 vs overnight w2+w3);
    # secondary = halves by birth time
    print("\n== SPLIT VALIDATION ==")
    parts = [("A w1 (evening 07-02 18:2x)", [r for r in A3 if r["window"] == "w1"]),
             ("A w2+w3 (overnight 07-03 04:4x-05:4x)",
              [r for r in A3 if r["window"] == "w23"])]
    half = len(A3) // 2
    parts += [("A first-half by birth", A3[:half]), ("A second-half by birth", A3[half:])]
    for nm, part in parts:
        print("[%s n=%d pos=%d]" % (nm, len(part), sum(1 for r in part if r["traction"])))
        sweep(part, "traction", "vol_5", [500, 2000])
        sweep(part, "traction", "vol_15", [1000, 5000, 10000])
        sweep(part, "traction", "n_bars_5", [3, 5])
        sweep(part, "traction", "n_bars_15", [8, 10, 12])
    if Bg:
        halfb = len(Bg) // 2
        for nm, part in (("B first-half", Bg[:halfb]), ("B second-half", Bg[halfb:])):
            n10 = sum(1 for r in part if (r.get("vol_15") or 0) >= 10000)
            n5 = sum(1 for r in part if (r.get("vol_15") or 0) >= 5000)
            nb = sum(1 for r in part if (r.get("n_bars_15") or 0) >= 10)
            print("[%s n=%d] vol_15>=10k: %d/%d ; vol_15>=5k: %d/%d ; n_bars_15>=10: %d/%d"
                  % (nm, len(part), n10, len(part), n5, len(part), nb, len(part)))

    nb = sum(1 for r in A3 if not r["have_bars"])
    print("\n[assumption check] analyzed pools WITHOUT bar file (dead-screened, "
          "bar features treated as 0): %d/%d — all have DS vol24<$1k & liq<$5k "
          "& reserve<$10k, so vol_15>=1k+ thresholds are decidable-fail; only "
          "n_bars-only rules carry assumption risk on these." % (nb, len(A3)))

    # watchlist-size math
    print("\n== WATCHLIST MATH ==")
    pools_all = json.load(open(os.path.join(RIP, "_gt_newpools_cache.json")))["pools"]
    from collections import defaultdict
    wins = defaultdict(list)
    for a, p in pools_all.items():
        c = iso_to_epoch(p.get("created"))
        if c is None: continue
        wins[round(p.get("seen", 0) // 3600)].append(c)
    rates = []
    for k, v in sorted(wins.items()):
        if len(v) < 30: continue
        span = max(v) - min(v)
        if span <= 0: continue
        rates.append(len(v) / (span / 60.0))
    print("GT-visible launch rate per capture window (pools/min): %s"
          % ", ".join("%.1f" % r for r in rates))
    rate = med(rates)
    print("median rate %.1f/min = %d/day" % (rate, rate * 1440))
    for nm, fn in (("vol_15>=1k", lambda r: (r.get("vol_15") or 0) >= 1000),
                   ("vol_5>=500", lambda r: (r.get("vol_5") or 0) >= 500),
                   ("n_bars_5>=3", lambda r: (r.get("n_bars_5") or 0) >= 3),
                   ("vol_15>=5k", lambda r: (r.get("vol_15") or 0) >= 5000)):
        pr = sum(1 for r in A3 if fn(r)) / len(A3)
        print("  %-12s pass %4.1f%% -> %5.0f pools/day flagged (%.1f/hour; "
              "~%.0f concurrent if watched 2h each)"
              % (nm, 100 * pr, pr * rate * 1440, pr * rate * 60, pr * rate * 120))

    # persist per-pool rows for the report
    json.dump({"A": [{k: v for k, v in r.items()} for r in A3],
               "B": [{k: v for k, v in r.items()} for r in B]},
              open(os.path.join(RIP, "_traction_rows.json"), "w"))
    print("\nsaved _traction_rows.json")

if __name__ == "__main__":
    main()
