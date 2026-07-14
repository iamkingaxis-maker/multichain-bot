"""Stability sweep for rh_deep_only (RH). Measurement only, no file edits/ships.
Reuses experiment_scorecard.load_rh_trips join semantics (sells grouped by
(bot_id,pool), split at fully==True, ret=sum(pnl_usd)/25*100), and adds per-trip
MFE/MAE reconstructed from the sell sequence + reason strings."""
import json, re, statistics, os
from collections import defaultdict

LEDGER = os.path.join(os.path.dirname(__file__), "robinhood_tapes", "rh_paper_trades.jsonl")
BOT = "rh_deep_only"
ENTRY_USD = 25.0

def _num(x):
    try:
        if x is None or isinstance(x, bool): return None
        return float(x)
    except (TypeError, ValueError): return None

rows = []
with open(LEDGER, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try: d = json.loads(line)
        except json.JSONDecodeError: continue
        if str(d.get("ts",""))[:4] == "1970": continue
        rows.append(d)

# buys indexed for hold-time join
buys_by_key = defaultdict(list)
for d in rows:
    if d.get("ev") == "buy" and d.get("bot_id") == BOT:
        buys_by_key[(d.get("bot_id"), d.get("pool"))].append(d)
for lst in buys_by_key.values():
    lst.sort(key=lambda x: x.get("ts",""))

sells_by_key = defaultdict(list)
for d in rows:
    if d.get("ev") == "sell" and d.get("bot_id") == BOT:
        sells_by_key[(d.get("bot_id"), d.get("pool"))].append(d)

PNL_RE = re.compile(r"pnl=(-?\d+(?:\.\d+)?)%")
PEAK_RE = re.compile(r"peak\((-?\d+(?:\.\d+)?)%\)")

def _ts(s):
    from datetime import datetime
    try: return datetime.fromisoformat(s)
    except Exception: return None

trips = []
for (bot, pool), sells in sells_by_key.items():
    sells.sort(key=lambda x: x.get("ts",""))
    cur = []
    for s in sells:
        cur.append(s)
        if s.get("fully"):
            pnl_usd = sum(_num(x.get("pnl_usd")) or 0.0 for x in cur)
            realized = pnl_usd / ENTRY_USD * 100.0
            # MFE / MAE reconstruction from the trip's sell sequence
            exec_pnls, reason_pnls, peaks = [], [], []
            for x in cur:
                p = _num(x.get("pnl_pct"))
                if p is not None: exec_pnls.append(p)
                r = x.get("reason") or ""
                m = PNL_RE.search(r)
                if m: reason_pnls.append(float(m.group(1)))
                pk = PEAK_RE.search(r)
                if pk: peaks.append(float(pk.group(1)))
            allv = exec_pnls + reason_pnls + peaks
            mfe = max(allv) if allv else realized
            mae = min(exec_pnls + reason_pnls) if (exec_pnls + reason_pnls) else realized
            # hold time
            first_ts = _ts(cur[0].get("ts","")); last_ts = _ts(cur[-1].get("ts",""))
            hold = None
            cands = [b for b in buys_by_key.get((bot,pool),[]) if b.get("ts","") <= cur[0].get("ts","")]
            if cands and first_ts:
                b_ts = _ts(cands[-1].get("ts",""))
                if b_ts and last_ts: hold = (last_ts - b_ts).total_seconds()
            trips.append({"token": pool, "realized": realized, "mfe": mfe,
                          "mae": mae, "hold": hold, "sell_time": cur[-1].get("ts","")})
            cur = []

print(f"reconstructed {len(trips)} closed trips over {len(set(t['token'] for t in trips))} tokens (bot={BOT})")

# ---- SCRUB rule: drop ret>0 & hold<10s ----
scrubbed = [t for t in trips if (t["hold"] is not None and t["realized"] > 0 and t["hold"] < 10)]
trips = [t for t in trips if not (t["hold"] is not None and t["realized"] > 0 and t["hold"] < 10)]
print(f"scrubbed {len(scrubbed)} trips (ret>0 & hold<10s); kept {len(trips)}")

# show trip table
print("\n token       realized   mfe     mae    hold(s)")
for t in sorted(trips, key=lambda x: x["sell_time"]):
    print(f" {t['token'][:10]}  {t['realized']:8.2f}  {t['mfe']:6.2f}  {t['mae']:7.2f}  {str(round(t['hold']) if t['hold'] is not None else '?'):>7}")

# ---- config model ----
def apply_config(trip, X, Y):
    """X = tighter TP (+X). Y = hard tail-cap (-Y). TP-first priority on double-hit.
    None X or None Y disables that leg."""
    mfe, mae, realized = trip["mfe"], trip["mae"], trip["realized"]
    tp_hit = (X is not None and mfe >= X)
    cap_hit = (Y is not None and mae <= -Y)
    if tp_hit:
        return X            # bank at TP (caps upside, rescues give-backs)
    if cap_hit:
        return -Y           # exit at cap (caps downside, kills sub-Y recoveries)
    return realized

def per_token_medians(vals_by_tok):
    return {tok: statistics.median(v) for tok, v in vals_by_tok.items() if v}

def metrics_for(trip_rets):
    """trip_rets: list of (token, modeled_ret). Returns stability dict."""
    by = defaultdict(list)
    for tok, r in trip_rets: by[tok].append(r)
    pt = per_token_medians(by)
    n = len(pt)
    meds = sorted(pt.values())
    kept = meds[:-2] if n > 2 else meds
    ex2 = statistics.median(kept) if kept else (statistics.median(meds) if meds else None)
    green = sum(1 for m in pt.values() if m > 0)
    cat = sum(1 for m in pt.values() if m < -20.0)
    disp = statistics.pstdev(meds) if n > 1 else 0.0
    mean = statistics.mean(meds) if meds else None
    return {"n_tokens": n, "ex2": round(ex2,2) if ex2 is not None else None,
            "mean": round(mean,2) if mean is not None else None,
            "pct_green": round(100*green/n,1) if n else None,
            "pct_cat": round(100*cat/n,1) if n else None,
            "disp": round(disp,2) if n else None,
            "meds": pt}

# baseline (no TP/cap modification)
base = metrics_for([(t["token"], t["realized"]) for t in trips])
print("\n=== BASELINE (observed realized, no cap/TP mod) ===")
print(base["n_tokens"], "tokens  ex2=", base["ex2"], " mean=", base["mean"],
      " green%=", base["pct_green"], " cat%=", base["pct_cat"], " disp=", base["disp"])

# ---- sweep ----
TPS = [None, 4, 5, 6, 8]
CAPS = [None, 10, 12, 15, 20]
results = []
print("\n=== SWEEP (per-token-median stability bar) ===")
print(f"{'TP':>4} {'cap':>4} | {'n':>3} {'ex2':>7} {'mean':>7} {'grn%':>6} {'cat%':>6} {'disp':>7}  dblhit")
for X in TPS:
    for Y in CAPS:
        rets = [(t["token"], apply_config(t, X, Y)) for t in trips]
        m = metrics_for(rets)
        dbl = sum(1 for t in trips if X is not None and Y is not None and t["mfe"]>=X and t["mae"]<=-Y)
        m.update({"TP": X, "cap": Y, "dblhit": dbl})
        results.append(m)
        xs = "none" if X is None else f"+{X}"
        ys = "none" if Y is None else f"-{Y}"
        print(f"{xs:>4} {ys:>4} | {m['n_tokens']:>3} {str(m['ex2']):>7} {str(m['mean']):>7} "
              f"{str(m['pct_green']):>6} {str(m['pct_cat']):>6} {str(m['disp']):>7}  {dbl}")

# ---- TRIP-LEVEL per-bot P&L view (what the GOAL calls 'per-bot volatility') ----
# per-token-median dispersion is structurally blind to the tail-cap here, so also
# show sum(trip rets) and trip-level pstdev, where the cap DOES bite.
print("\n=== TRIP-LEVEL per-bot P&L (sum of modeled trip rets, %) + trip stdev ===")
print(f"{'TP':>4} {'cap':>4} | {'sum%':>9} {'trip_pstdev':>12} {'worst_trip':>11}")
for X in TPS:
    for Y in CAPS:
        rets = [apply_config(t, X, Y) for t in trips]
        s = sum(rets); sd = statistics.pstdev(rets) if len(rets)>1 else 0.0
        xs = "none" if X is None else f"+{X}"; ys = "none" if Y is None else f"-{Y}"
        print(f"{xs:>4} {ys:>4} | {s:9.1f} {sd:12.2f} {min(rets):11.2f}")

# ---- pick: minimize dispersion s.t. mean>=0 and ex2>=0 ----
elig = [m for m in results if m["mean"] is not None and m["mean"] >= 0 and m["ex2"] is not None and m["ex2"] >= 0]
elig.sort(key=lambda m: (m["disp"], -m["ex2"]))
print(f"\n{len(elig)} eligible combos (mean>=0 & ex2>=0)")
if elig:
    best = elig[0]
    xs = "none" if best["TP"] is None else f"+{best['TP']}"
    ys = "none" if best["cap"] is None else f"-{best['cap']}"
    print(f"BEST (min dispersion): TP={xs} cap={ys}  ex2={best['ex2']} mean={best['mean']} "
          f"grn%={best['pct_green']} cat%={best['pct_cat']} disp={best['disp']}  "
          f"dispersion_vs_baseline={round(best['disp']-base['disp'],2)}")
else:
    best = None
    print("NO eligible combo (mean>=0 & ex2>=0) -> nominating min-dispersion @ mean>=0 for OOS char.")

# nominate a config for OOS characterization even if strict-infeasible:
# among mean>=0, min token-median dispersion, tie-break highest ex2, then tightest cap.
nom_pool = [m for m in results if m["mean"] is not None and m["mean"] >= 0]
nom_pool.sort(key=lambda m: (m["disp"], -m["ex2"], (m["cap"] or 999)))
nominated = best if best is not None else nom_pool[0]

# ---- OOS four-half: chrono-half x token-parity ----
if nominated is not None:
    X, Y = nominated["TP"], nominated["cap"]
    xs = "none" if X is None else f"+{X}"; ys = "none" if Y is None else f"-{Y}"
    print(f"\nNominated config for OOS: TP={xs} cap={ys} "
          f"(ex2={nominated['ex2']} mean={nominated['mean']} disp={nominated['disp']})")
    # chrono split by median sell_time
    st_sorted = sorted(trips, key=lambda t: t["sell_time"])
    mid = len(st_sorted)//2
    cutoff = st_sorted[mid]["sell_time"]
    # token parity: stable assignment by sorted token order
    toks = sorted(set(t["token"] for t in trips))
    parity = {tok: i % 2 for i, tok in enumerate(toks)}
    def quarter(t):
        chrono = 0 if t["sell_time"] < cutoff else 1
        return (chrono, parity[t["token"]])
    quarters = defaultdict(list)
    for t in trips:
        quarters[quarter(t)].append(t)
    print("\n=== OOS four-half (chrono x parity), best config ===")
    green_q = 0
    for q in sorted(quarters):
        rets = [(t["token"], apply_config(t, X, Y)) for t in quarters[q]]
        m = metrics_for(rets)
        ok = (m["ex2"] is not None and m["ex2"] >= 0)
        green_q += 1 if ok else 0
        print(f" quarter {q}: n_trips={len(quarters[q])} n_tok={m['n_tokens']} "
              f"ex2={m['ex2']} green%={m['pct_green']}  {'GREEN' if ok else 'red'}")
    print(f"oos_quarters_green = {green_q}/4")

    # final stable verdict (on the nominated config)
    nm = nominated
    stable = (nm["ex2"] >= 0 and nm["pct_green"] >= 55 and nm["pct_cat"] <= 5 and green_q >= 3)
    print(f"\nSTABLE = {stable}  (ex2>=0:{nm['ex2']>=0}, grn>=55:{nm['pct_green']>=55}, "
          f"cat<=5:{nm['pct_cat']<=5}, oos>=3:{green_q>=3})")
    print(f"eligible_strict (mean>=0 & ex2>=0 exists): {best is not None}")
