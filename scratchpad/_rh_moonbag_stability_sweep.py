"""RH rh_moonbag stability sweep (READ-ONLY, measurement only).
Models tail-cap x TP exit configs from per-trip realized/MFE/MAE proxies and
scores the STABILITY bar on per-token medians. No files shipped/edited."""
import json, statistics as st
from collections import defaultdict

PATH = "scratchpad/robinhood_tapes/rh_paper_trades.jsonl"
BOT = "rh_moonbag"
ENTRY_USD = 25.0

rows = []
with open(PATH, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(d.get("ts", ""))[:4] == "1970":
            continue
        rows.append(d)

# buys per pool (for hold-time / scrub)
from datetime import datetime
def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

buys_by_pool = defaultdict(list)
for d in rows:
    if d.get("ev") == "buy" and d.get("bot_id") == BOT:
        t = parse_ts(d.get("ts"))
        if t is not None:
            buys_by_pool[d.get("pool")].append(t)
for v in buys_by_pool.values():
    v.sort()

# reconstruct trips (mirror load_rh_trips join: group sells by pool, split at fully)
sells_by = defaultdict(list)
for d in rows:
    if d.get("ev") == "sell" and d.get("bot_id") == BOT:
        sells_by[d.get("pool")].append(d)

trips = []
for pool, ss in sells_by.items():
    ss.sort(key=lambda x: x.get("ts", ""))
    cur = []
    for s in ss:
        cur.append(s)
        if s.get("fully"):
            pnl = sum((x.get("pnl_usd") or 0.0) for x in cur)
            ret = pnl / ENTRY_USD * 100.0
            pnls = [(x.get("pnl_pct") or 0.0) for x in cur]
            peak = max(pnls)      # MFE proxy (realized-price high; lower bound)
            mae = min(pnls)       # MAE proxy (realized-price low; lower bound on drawdown)
            first_pnl = cur[0].get("pnl_pct") or 0.0
            last_ts = parse_ts(cur[-1].get("ts"))
            first_ts = parse_ts(cur[0].get("ts"))
            # hold: last sell - latest buy preceding first sell
            bt = [b for b in buys_by_pool.get(pool, []) if first_ts is None or b <= (first_ts + 1)]
            hold = (last_ts - bt[-1]) if (bt and last_ts) else None
            trips.append(dict(token=pool, ret=ret, peak=peak, mae=mae,
                              first_pnl=first_pnl, last_ts=last_ts, hold=hold))
            cur = []

# SCRUB: drop ret>0 & hold<10s
before = len(trips)
trips = [t for t in trips if not (t["ret"] > 0 and t["hold"] is not None and t["hold"] < 10)]
scrubbed = before - len(trips)

def model_ret(t, Y, X):
    """Y = tail-cap magnitude (exit ~ -Y if touched). X = TP level (bank ~X if peak reached).
       Y or X = None disables that leg. Both-fire resolved by first-sell direction (path order)."""
    R, P, M, fp = t["ret"], t["peak"], t["mae"], t["first_pnl"]
    stop = (Y is not None) and (M <= -Y)
    tp = (X is not None) and (P >= X)
    if not stop and not tp:
        return R
    if tp and not stop:
        return float(X)
    if stop and not tp:
        return float(-Y)
    # both fire: use path order (first realized sell sign)
    return float(X) if fp >= 0 else float(-Y)

def per_token_medians(ts, Y, X):
    by = defaultdict(list)
    for t in ts:
        by[t["token"]].append(model_ret(t, Y, X))
    return {k: st.median(v) for k, v in by.items()}

def metrics(ts, Y, X):
    pt = per_token_medians(ts, Y, X)
    meds = sorted(pt.values())
    n = len(meds)
    if n == 0:
        return None
    kept = meds[:-2] if n > 2 else meds
    ex2 = st.median(kept)
    green = sum(1 for m in meds if m > 0)
    cat = sum(1 for m in meds if m < -20.0)
    disp = st.pstdev(meds) if n > 1 else 0.0
    mean_med = st.mean(meds)
    all_rets = [model_ret(t, Y, X) for t in ts]
    return dict(n_tokens=n, ex2=round(ex2, 2), plain_med=round(st.median(meds), 2),
                pct_green=round(100 * green / n, 1), pct_cat=round(100 * cat / n, 1),
                disp=round(disp, 2), mean_med=round(mean_med, 2),
                mean_trip=round(st.mean(all_rets), 2))

base = metrics(trips, None, None)
print("=== BASELINE (uncapped, realized) ===")
print(base)
print("n trips:", len(trips), "scrubbed:", scrubbed, "distinct tokens:", len(set(t['token'] for t in trips)))
print()

CAPS = [10, 12, 15, 20]
TPS = [4, 5, 6, 8]
grid = []
print("=== GRID (cap x TP) : ex2 | mean_med | %grn | cat% | disp | mean_trip ===")
for Y in CAPS:
    for X in TPS:
        m = metrics(trips, Y, X)
        grid.append((Y, X, m))
        print(f"cap=-{Y:<3} TP=+{X}:  ex2={m['ex2']:>6}  mmed={m['mean_med']:>6}  "
              f"grn={m['pct_green']:>5}  cat={m['pct_cat']:>5}  disp={m['disp']:>6}  mtrip={m['mean_trip']:>6}")

# eligible: mean_med>=0 AND ex2>=0 ; pick min dispersion
elig = [(Y, X, m) for (Y, X, m) in grid if m["mean_med"] >= 0 and m["ex2"] >= 0]
print("\neligible (mean_med>=0 & ex2>=0):", len(elig))
if elig:
    best = min(elig, key=lambda z: (z[2]["disp"], -z[2]["ex2"]))
else:
    # fall back: min dispersion among ex2>=0 only, else overall min disp with best mean
    pool2 = [(Y, X, m) for (Y, X, m) in grid if m["ex2"] >= 0]
    best = min(pool2 or grid, key=lambda z: (z[2]["disp"], -z[2]["mean_med"]))
Y, X, bm = best
print(f"\n=== BEST: cap=-{Y} / TP=+{X} ===")
print(bm)
print("dispersion vs baseline:", round(bm["disp"] - base["disp"], 2))

# ---- OOS: chrono-half x token-parity = 4 quarters ----
tsorted = sorted([t for t in trips if t["last_ts"] is not None], key=lambda t: t["last_ts"])
mid = len(tsorted) // 2
half = {id(t): ("early" if i < mid else "late") for i, t in enumerate(tsorted)}
toks = sorted(set(t["token"] for t in trips))
parity = {tok: i % 2 for i, tok in enumerate(toks)}
quarters = defaultdict(list)
for t in tsorted:
    quarters[(half[id(t)], parity[t["token"]])].append(t)

print("\n=== OOS 4 quarters (chrono-half x token-parity) at BEST config ===")
green_q = 0
for key in [("early", 0), ("early", 1), ("late", 0), ("late", 1)]:
    q = quarters.get(key, [])
    if not q:
        print(f"  {key}: EMPTY")
        continue
    m = metrics(q, Y, X)
    ok = m["ex2"] >= 0
    green_q += 1 if ok else 0
    print(f"  {key}: n_tok={m['n_tokens']} ntrips={len(q)} ex2={m['ex2']} grn={m['pct_green']} "
          f"cat={m['pct_cat']} -> {'GREEN' if ok else 'red'}")
print("oos_quarters_green:", green_q, "/4")

stable = (bm["ex2"] >= 0 and bm["pct_green"] >= 55 and bm["pct_cat"] <= 5 and green_q >= 3)
print("\nSTABLE:", stable, "(ex2>=0 &", "grn>=55 &", "cat<=5 &", "oos>=3)")
print(f"n_tokens={bm['n_tokens']} (<20 => DIRECTIONAL ONLY)")
