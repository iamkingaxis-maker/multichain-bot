"""Grade candidate holder-structure rules on winners / universe / HOODLANA-at-entry.
Corrected features: pool vaults identified via topHolders.owner/address in
{markets[].pubkey, liquidityA/BAccount, our pair_address} or Raydium V4 authority."""
import json, os, glob, collections

BASE = os.path.dirname(os.path.abspath(__file__))
SP = os.path.dirname(BASE)
RAY = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"

trades = json.load(open(os.path.join(SP, "_full_trades.json")))
pairs = collections.defaultdict(set)
for x in trades:
    if x.get("address") and x.get("pair_address"):
        pairs[x["address"]].add(x["pair_address"])

coh = json.load(open(os.path.join(BASE, "cohorts.json")))
winners_any = set(json.load(open(os.path.join(BASE, "winners_anybot.json"))))
winners_strict = set(coh["winners_all"])
uni = {u["mint"] for u in coh["universe_recent50"]}
ds = json.load(open(os.path.join(SP, "rug_forensics", "death_split.json")))
alive = set(ds["alive"])

rows = {}
for f in glob.glob(os.path.join(BASE, "raw", "*.json")):
    m = os.path.basename(f)[:-5]
    d = json.load(open(f))
    th = d.get("topHolders") or []
    pool_set = set(pairs.get(m, set()))
    for mk in (d.get("markets_lp") or []):
        for k in ("pubkey", "liquidityA", "liquidityB"):
            v = mk.get(k)
            if isinstance(v, str) and v:
                pool_set.add(v)

    def is_pool(h):
        return (h.get("owner") in pool_set or h.get("address") in pool_set
                or h.get("owner") == RAY)

    pool_pct = sum(float(h.get("pct") or 0) for h in th if is_pool(h))
    insiders = [h for h in th if h.get("insider") is True]
    insider_pct = sum(float(h.get("pct") or 0) for h in insiders)
    real = [h for h in th if not is_pool(h) and h.get("insider") is not True]
    top10 = sum(float(h.get("pct") or 0) for h in real[:10])
    shoulder = sum(float(h.get("pct") or 0) for h in real[10:20])
    hidden = 100.0 - pool_pct - top10 - insider_pct
    rows[m] = {
        "mint": m,
        "pool_pct": round(pool_pct, 2),
        "top10": round(top10, 2),
        "shoulder_11_20": round(shoulder, 2),
        "insider_pct": round(insider_pct, 2),
        "hidden_share": round(hidden, 2),
        "total_holders": d.get("totalHolders"),
        "graph_insiders": d.get("graphInsidersDetected"),
        "n_topholders": len(th),
        "alive": m in alive,
        "is_winner_any": m in winners_any,
        "is_winner_strict": m in winners_strict,
        "is_universe": m in uni,
    }

json.dump(rows, open(os.path.join(BASE, "features_corrected.json"), "w"), indent=1)

W_any = [r for r in rows.values() if r["is_winner_any"] and r["alive"]]
W_str = [r for r in rows.values() if r["is_winner_strict"] and r["alive"]]
U = [r for r in rows.values() if r["is_universe"]]
U_alive = [r for r in U if r["alive"]]

# HOODLANA at entry (reconstructed): pool 12.45-12.78 -> use 12.45 (t_0220, inside entry window);
# recorded top10 = 14.71; insider/shoulder/total_holders at entry unknown.
HOOD = {"pool_pct": 12.45, "top10": 14.71, "hidden_share": round(100 - 12.45 - 14.71, 2)}

def rate(cohort, pred):
    n = len(cohort)
    k = sum(1 for r in cohort if pred(r))
    return k, n, (100.0 * k / n if n else float("nan"))

RULES = []
for y in (40, 50, 60, 65, 70):
    RULES.append((f"hidden_share >= {y}", lambda r, y=y: r["hidden_share"] >= y,
                  HOOD["hidden_share"] >= y))
for y in (50, 60, 70):
    for z in (1000, 2000, 5000):
        RULES.append((f"hidden >= {y} AND total_holders < {z}",
                      lambda r, y=y, z=z: r["hidden_share"] >= y and (r["total_holders"] or 0) < z,
                      HOOD["hidden_share"] >= y))  # HOODLANA total_holders at entry surely < z (mins old)
for x in (5, 10, 15, 20):
    RULES.append((f"shoulder_11_20 >= {x}", lambda r, x=x: r["shoulder_11_20"] >= x, None))
for w in (5, 10, 20):
    RULES.append((f"insider_pct >= {w}", lambda r, w=w: r["insider_pct"] >= w, None))
for w in (20, 50, 100):
    RULES.append((f"graph_insiders >= {w}", lambda r, w=w: (r["graph_insiders"] or 0) >= w, None))
RULES.append(("pool<20 AND top10<25 (both small => mass hidden)",
              lambda r: r["pool_pct"] < 20 and r["top10"] < 25,
              HOOD["pool_pct"] < 20 and HOOD["top10"] < 25))

print(f"cohorts: winners_any_alive n={len(W_any)}  winners_strict_alive n={len(W_str)}  "
      f"universe n={len(U)} (alive {len(U_alive)})")
print(f"HOODLANA at entry: pool={HOOD['pool_pct']} top10={HOOD['top10']} hidden={HOOD['hidden_share']}")
print()
hdr = f"{'rule':48s} {'killA%':>7s} {'killS%':>7s} {'uniBlk%':>8s} {'uniAlv%':>8s} {'HOOD':>5s}"
print(hdr)
print("-" * len(hdr))
results = []
for name, pred, hood in RULES:
    ka = rate(W_any, pred); ks = rate(W_str, pred); ub = rate(U, pred); ua = rate(U_alive, pred)
    hoodtxt = {True: "YES", False: "no", None: "?"}[hood]
    print(f"{name:48s} {ka[2]:6.1f}({ka[0]:2d}) {ks[2]:5.1f}({ks[0]:2d}) {ub[2]:6.1f}({ub[0]:2d}) "
          f"{ua[2]:6.1f}({ua[0]:2d}) {hoodtxt:>5s}")
    results.append({"rule": name, "winner_kill_any_pct": round(ka[2], 1), "kill_any_n": ka[0],
                    "winner_kill_strict_pct": round(ks[2], 1), "kill_strict_n": ks[0],
                    "universe_block_pct": round(ub[2], 1), "universe_alive_block_pct": round(ua[2], 1),
                    "hood_caught": hood})
json.dump({"cohort_sizes": {"winners_any_alive": len(W_any), "winners_strict_alive": len(W_str),
                            "universe": len(U), "universe_alive": len(U_alive)},
           "hoodlana_entry": HOOD, "rules": results},
          open(os.path.join(BASE, "grade_results.json"), "w"), indent=1)
print("\nsaved grade_results.json + features_corrected.json")

# distribution snapshots for the report
import statistics as st
def dist(c, k):
    v = sorted(x[k] for x in c if x[k] is not None)
    if not v:
        return "n/a"
    return (f"min {v[0]:.1f} p25 {v[len(v)//4]:.1f} med {st.median(v):.1f} "
            f"p75 {v[3*len(v)//4]:.1f} max {v[-1]:.1f}")
for k in ("hidden_share", "pool_pct", "top10", "shoulder_11_20", "insider_pct"):
    print(f"\n{k}: winners_any_alive: {dist(W_any, k)}")
    print(f"{k}: universe:          {dist(U, k)}")

# who exactly does the leading joint rule kill / block?
for y, z in ((60, 1000), (70, 1000), (70, 2000)):
    pred = lambda r: r["hidden_share"] >= y and (r["total_holders"] or 0) < z
    print(f"\n--- hidden>={y} & holders<{z} ---")
    for r in rows.values():
        if pred(r) and (r["is_winner_any"] and r["alive"] or r["is_universe"]):
            print(f"  {r['mint'][:10]} hidden={r['hidden_share']} holders={r['total_holders']} "
                  f"pool={r['pool_pct']} top10={r['top10']} alive={r['alive']} "
                  f"winnerA={r['is_winner_any']} winnerS={r['is_winner_strict']} uni={r['is_universe']}")
