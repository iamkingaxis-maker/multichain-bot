"""Population-scale maker-less analyses from the full sweep tape (10.4M swaps).
- pool-death census: -90%-from-peak collapse rate by creation day, time-to-death
- launch quality: pools with real volume vs spam, volume concentration in first hour
- daily chain USD volume
Writes rh_history/population_stats.json
"""
import json, os, gzip, time, bisect, collections

OUT = r"C:\Users\jcole\multichain-bot\scratchpad\rh_history"

anchors = json.load(open(os.path.join(OUT, "anchors.json")))
A_B = [a[0] for a in anchors]; A_T = [a[1] for a in anchors]
def est_ts(block):
    i = bisect.bisect_right(A_B, block) - 1
    if i < 0: return A_T[0]
    if i >= len(A_B) - 1: return A_T[-1] + (block - A_B[-1]) * 0.1
    b0, t0_, b1, t1 = A_B[i], A_T[i], A_B[i+1], A_T[i+1]
    return t0_ + (t1 - t0_) * (block - b0) / max(1, b1 - b0)

curve = json.load(open(os.path.join(OUT, "eth_price_curve.json")))
daily_px = json.load(open(os.path.join(OUT, "eth_daily_usd.json")))
C_B = [c[0] for c in curve]; C_P = [c[1] for c in curve]
def eth_usd(block):
    if C_B and block >= C_B[0]:
        i = min(bisect.bisect_right(C_B, block) - 1, len(C_B) - 1)
        return C_P[max(0, i)]
    day = time.strftime("%Y-%m-%d", time.gmtime(est_ts(block)))
    return daily_px.get(day) or (C_P[0] if C_P else 1750.0)

reg_day = {}
reg_block = {}
for line in open(os.path.join(OUT, "pools_registry.jsonl"), encoding="utf-8"):
    d = json.loads(line)
    reg_day[d["pool"]] = time.strftime("%Y-%m-%d", time.gmtime(d["ts"]))
    reg_block[d["pool"]] = d["block"]

# per-pool state: [first_blk, last_blk, peak_px, peak_blk, last_px, cross30_blk,
#                  vol_eth, vol_first_hr_eth, n]
P = {}
day_vol = collections.Counter()   # day -> eth vol
t0 = time.time()
n = 0
with gzip.open(os.path.join(OUT, "sweep_logs.jsonl.gz"), "rt", encoding="utf-8") as f:
    for ln in f:
        n += 1
        try:
            d = json.loads(ln)
        except Exception:
            continue
        p = d["p"]; b = d["b"]; px = d["px"] or 0.0; w = d["w"]
        s = P.get(p)
        if s is None:
            s = P[p] = [b, b, 0.0, b, 0.0, None, 0.0, 0.0, 0]
        s[1] = max(s[1], b)
        s[6] += w
        s[8] += 1
        if px > 0:
            if px > s[2]:
                s[2] = px; s[3] = b
            elif s[2] > 0 and px < 0.3 * s[2] and s[5] is None and s[8] > 5:
                s[5] = b
            s[4] = px
        cb = reg_block.get(p, s[0])
        if (b - cb) * 0.15 <= 3600:  # ~<=1h of blocks (0.1-1s spb; rough)
            s[7] += w
        if n % 2_000_000 == 0:
            print(f"[pop] {n} rows {time.time()-t0:.0f}s", flush=True)

print(f"[pop] read {n} rows in {time.time()-t0:.0f}s; pools={len(P)}", flush=True)

# day volume (usd) second pass over P is impossible; approximate via per-pool? -> do per-row day agg in same pass normally.
# We aggregate day vol from pool first/last instead -> too coarse; use hour_stats already saved.
hs = json.load(open(os.path.join(OUT, "sweep_counts.json")))["hour_stats"]
day_usd = collections.Counter()
for k, v in hs.items():
    day = k[:10]
    blkless_px = daily_px.get(day, 1750.0)
    day_usd[day] += (v[1] + v[2]) * blkless_px

collapse_by_day = collections.defaultdict(lambda: [0, 0, 0])  # day -> [n_real, n_collapsed, n_spam]
tt_death = []
for p, s in P.items():
    day = reg_day.get(p, "?")
    vol_usd_est = s[6] * daily_px.get(day, 1750.0)
    if s[8] < 30 or vol_usd_est < 500:
        collapse_by_day[day][2] += 1
        continue
    collapse_by_day[day][0] += 1
    dead = s[2] > 0 and s[4] < 0.1 * s[2]
    if dead and s[5]:
        collapse_by_day[day][1] += 1
        tt_death.append(est_ts(s[5]) - est_ts(reg_block.get(p, s[0])))

tt_death.sort()
def pctl(v, q):
    return v[min(len(v)-1, int(q*len(v)))] if v else None
res = {
    "n_rows": n, "n_pools_swapped": len(P),
    "day_usd_volume": {k: round(v, 0) for k, v in sorted(day_usd.items())},
    "collapse_census": {d: {"real": v[0], "collapsed_90pct": v[1], "spam_low_vol": v[2]}
                        for d, v in sorted(collapse_by_day.items())},
    "time_to_death_hours": {
        "n": len(tt_death),
        "p25": round(pctl(tt_death, .25)/3600, 2) if tt_death else None,
        "p50": round(pctl(tt_death, .50)/3600, 2) if tt_death else None,
        "p75": round(pctl(tt_death, .75)/3600, 2) if tt_death else None},
}
json.dump(res, open(os.path.join(OUT, "population_stats.json"), "w"), indent=1)
tot_real = sum(v[0] for v in collapse_by_day.values())
tot_coll = sum(v[1] for v in collapse_by_day.values())
tot_spam = sum(v[2] for v in collapse_by_day.values())
print(f"[pop] real pools={tot_real} collapsed={tot_coll} ({100*tot_coll/max(1,tot_real):.0f}%) "
      f"spam/dust={tot_spam}", flush=True)
for d in sorted(collapse_by_day):
    v = collapse_by_day[d]
    if v[0] + v[2] < 20: continue
    print(f"  {d}: real={v[0]:5d} collapsed={v[1]:5d} ({100*v[1]/max(1,v[0]):3.0f}%) spam={v[2]:6d}", flush=True)
print(f"[pop] time-to-death p50={res['time_to_death_hours']['p50']}h (n={len(tt_death)})", flush=True)
print("[pop] wrote population_stats.json", flush=True)
