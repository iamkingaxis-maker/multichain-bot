"""Step 4: grade hidden_min x holders_max grid on three evidence planes:
  A) AT-ENTRY stamped slice (n=27, 2026-07-11, same-day forward outcomes) — gold standard
  B) current-state alive winners (winner-kill; current~entry approximation)
  C) current-state alive universe (block-rate)
Plus: post-rug signature check on catastrophic cohort (label validation only),
HOODLANA-at-entry (hidden=72.84, holders O(100), assumption holders<1000).
"""
import json, os, glob, statistics as st

REPO = r"C:\Users\jcole\multichain-bot"
V2 = os.path.join(REPO, "scratchpad", "rug_cohort_v2")
RAY = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"

labels = {}
for line in open(os.path.join(V2, "labels_final.jsonl"), encoding="utf-8"):
    r = json.loads(line)
    labels[r["mint"]] = r
win = json.load(open(os.path.join(V2, "winners.json")))
anybot, strict = set(win["anybot"]), set(win["strict"])
uni = {u["mint"]: u for u in json.load(open(os.path.join(V2, "mint_universe.json")))}

# ---- compute current-state features from raw/ (vault-join pool identification) ----
rows = {}
for f in glob.glob(os.path.join(V2, "raw", "*.json")):
    m = os.path.basename(f)[:-5]
    d = json.load(open(f, encoding="utf-8"))
    th = d.get("topHolders") or []
    pool_set = set()
    pa = (uni.get(m) or {}).get("pair_address") or (labels.get(m) or {}).get("pair_address")
    if pa:
        pool_set.add(pa)
    for mk in (d.get("markets_lp") or []):
        for k in ("pubkey", "liquidityA", "liquidityB", "liquidityAAccount", "liquidityBAccount"):
            v = mk.get(k)
            if isinstance(v, str) and v:
                pool_set.add(v)

    def is_pool(h):
        return (h.get("owner") in pool_set or h.get("address") in pool_set
                or h.get("owner") == RAY)

    pool_pct = sum(float(h.get("pct") or 0) for h in th if is_pool(h))
    real = [h for h in th if not is_pool(h) and h.get("insider") is not True]
    top10 = sum(float(h.get("pct") or 0) for h in real[:10])
    hidden = 100.0 - pool_pct - top10
    lab = labels.get(m, {})
    rows[m] = {"mint": m, "pool_pct": round(pool_pct, 2), "top10": round(top10, 2),
               "hidden": round(hidden, 2), "holders": d.get("totalHolders"),
               "label": lab.get("label"), "ret_pct": lab.get("ret_pct"),
               "is_winner_any": m in anybot, "is_winner_strict": m in strict}
json.dump(rows, open(os.path.join(V2, "features_current.json"), "w"), indent=0)

W_any = [r for r in rows.values() if r["label"] == "alive" and r["is_winner_any"]]
W_str = [r for r in rows.values() if r["label"] == "alive" and r["is_winner_strict"]]
U_alive = [r for r in rows.values() if r["label"] == "alive"]
CAT = [r for r in rows.values() if r["label"] == "catastrophic"]

print(f"current-state cohorts: alive_winners_any n={len(W_any)}  strict n={len(W_str)}  "
      f"alive_universe n={len(U_alive)}  catastrophic(post-rug) n={len(CAT)}")

# ---- post-rug signature check (label validation, NOT rule grading) ----
if CAT:
    pv = sorted(r["pool_pct"] for r in CAT)
    hv = sorted(r["holders"] or 0 for r in CAT)
    big_pool = sum(1 for r in CAT if r["pool_pct"] >= 80)
    print(f"\npost-rug catastrophic signature: pool_pct med={st.median(pv):.1f} "
          f"p25={pv[len(pv)//4]:.1f} p75={pv[3*len(pv)//4]:.1f}; "
          f"pool>=80% in {big_pool}/{len(CAT)} ({100*big_pool/len(CAT):.0f}%) — "
          f"HOODLANA-class hidden-supply-dump share")
    print(f"post-rug holders: med={st.median(hv)}")

# ---- at-entry stamped slice (forward-from-stamp outcomes; ALL same-day => provisional) ----
stamped = json.load(open(os.path.join(V2, "stamped_entries.json")))
S = [s for s in stamped if s["hidden"] is not None and s["holders"] is not None]
for s in S:
    s["ret_pct"] = s.get("fwd_ret")
S_cat = [s for s in S if s.get("label_fwd") == "catastrophic"]
S_win = [s for s in S if (s.get("fwd_ret") or 0) >= 20]  # same-day winners
print(f"\nat-entry stamped slice n={len(S)}: catastrophic={len(S_cat)} "
      f"({[x['token'] for x in S_cat]}), same-day-winners(ret>=+20)={len(S_win)} "
      f"({[(x['token'], x['ret_pct']) for x in S_win]})")

HOOD = {"hidden": 72.84, "holders_assumed": 300}  # O(100-500), inferred not measured
LIZ = next((s for s in S if s["token"] == "LIZARD"), None)

# ---- grid ----
HID = (55, 60, 65, 70, 75, 80)
HOLD = (500, 700, 1000, 1500, 2000, 2500, 3000, None)

def blocked(r, y, z, hk="hidden", zk="holders"):
    if r[hk] is None:
        return False
    return r[hk] >= y and (z is None or (r[zk] or 0) < z)

def pct(coh, y, z):
    n = len(coh)
    k = sum(1 for r in coh if blocked(r, y, z))
    return k, n, (100.0 * k / n if n else float("nan"))

grid = []
hdr = (f"{'cell':26s} {'S:cat':>6s} {'S:winKill':>9s} {'S:blk':>6s} {'LIZ':>4s} "
       f"{'killA%':>10s} {'killS%':>10s} {'uniBlk%':>10s} {'HOOD':>5s}")
print("\n" + hdr)
print("-" * len(hdr))
for y in HID:
    for z in HOLD:
        scat = sum(1 for s in S_cat if blocked(s, y, z))
        swk = sum(1 for s in S_win if blocked(s, y, z))
        sblk = sum(1 for s in S if blocked(s, y, z))
        liz = LIZ is not None and blocked(LIZ, y, z)
        ka = pct(W_any, y, z)
        ks = pct(W_str, y, z)
        ua = pct(U_alive, y, z)
        hood = HOOD["hidden"] >= y and (z is None or HOOD["holders_assumed"] < z)
        zs = "none" if z is None else str(z)
        print(f"hid>={y:2d} hold<{zs:>5s}        {scat}/{len(S_cat)}   {swk}/{len(S_win)}     "
              f"{sblk:2d}/{len(S)} {'YES' if liz else 'no':>4s} "
              f"{ka[2]:5.1f}({ka[0]:3d}) {ks[2]:5.1f}({ks[0]:3d}) {ua[2]:5.1f}({ua[0]:3d}) "
              f"{'YES' if hood else 'no':>5s}")
        grid.append({"hidden_min": y, "holders_max": z, "stamped_cat_caught": scat,
                     "stamped_cat_n": len(S_cat), "stamped_winner_kill": swk,
                     "stamped_winner_n": len(S_win), "stamped_blocked": sblk,
                     "stamped_n": len(S), "lizard_blocked": liz,
                     "winner_kill_any_pct": round(ka[2], 1), "winner_kill_any_k": ka[0],
                     "winner_kill_any_n": ka[1],
                     "winner_kill_strict_pct": round(ks[2], 1),
                     "universe_alive_block_pct": round(ua[2], 1),
                     "hood_caught_assumed": hood})
json.dump({"grid": grid,
           "cohort_sizes": {"alive_winners_any": len(W_any), "alive_winners_strict": len(W_str),
                            "alive_universe": len(U_alive), "catastrophic_postrug": len(CAT),
                            "stamped": len(S), "stamped_cat": len(S_cat),
                            "stamped_winners": len(S_win)}},
          open(os.path.join(V2, "grade_grid.json"), "w"), indent=1)

# two-branch composite: (hid>=Y2 any) OR (hid>=60 & hold<Z)
print("\n--- two-branch composites: hid>=Y2(any holders) OR hid>=60 & hold<Z ---")
for y2, z in ((80, 500), (80, 700), (80, 1000), (75, 500), (75, 700), (75, 1000)):
    def blk(r, y2=y2, z=z):
        return blocked(r, y2, None) or blocked(r, 60, z)
    scat = sum(1 for s in S_cat if blk(s))
    swk = [s["token"] for s in S_win if blk(s)]
    ka = (sum(1 for r in W_any if blk(r)), len(W_any))
    ks = (sum(1 for r in W_str if blk(r)), len(W_str))
    ua = (sum(1 for r in U_alive if blk(r)), len(U_alive))
    liz = LIZ is not None and blk(LIZ)
    hood = HOOD["hidden"] >= y2 or HOOD["holders_assumed"] < z
    print(f"Y2={y2} Z={z}: S_cat {scat}/{len(S_cat)} S_winKill={swk} LIZ={'YES' if liz else 'no'} "
          f"killA={100*ka[0]/ka[1]:.1f}%({ka[0]}/{ka[1]}) killS={100*ks[0]/ks[1]:.1f}% "
          f"uniBlk={100*ua[0]/ua[1]:.1f}% HOOD={'YES' if hood else 'no'}")

# who does each notable cell kill among alive winners (names for the report)
print("\n--- alive-winner kills, cell hid>=60 & hold<1000 (current state) ---")
for r in W_any:
    if blocked(r, 60, 1000):
        print(f"  {r['mint'][:10]} hidden={r['hidden']} holders={r['holders']} ret={r['ret_pct']}")
print("--- alive-winner kills, branch hid>=80 any-holders (current state) ---")
for r in W_any:
    if blocked(r, 80, None):
        print(f"  {r['mint'][:10]} hidden={r['hidden']} holders={r['holders']} ret={r['ret_pct']}")
