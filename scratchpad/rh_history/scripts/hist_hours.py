"""Hour rulebook at FULL population scale: volume / new pools / net flow by UTC
hour across all days, from sweep_counts.json (every WETH-pool swap on chain)."""
import json, os, time, collections

OUT = r"C:\Users\jcole\multichain-bot\scratchpad\rh_history"
C = json.load(open(os.path.join(OUT, "sweep_counts.json")))
hs = C["hour_stats"]  # "YYYY-MM-DDTHH" -> [n, buy_eth, sell_eth, _]

new_pools = collections.Counter()
for line in open(os.path.join(OUT, "pools_registry.jsonl"), encoding="utf-8"):
    d = json.loads(line)
    new_pools[time.strftime("%Y-%m-%dT%H", time.gmtime(d["ts"]))] += 1

days = sorted({k[:10] for k in hs})
days = [d for d in days if d >= "2026-07-01"]
print("per-hour across days (vol = buy+sell ETH; nf = buy-sell ETH; np = new pools)")
hdr = "hour | " + " | ".join(d[5:] for d in days)
print(hdr)
hour_rank = collections.defaultdict(list)
for h in range(24):
    cells = []
    for d in days:
        k = f"{d}T{h:02d}"
        v = hs.get(k)
        n, b, s = (v[0], v[1], v[2]) if v else (0, 0.0, 0.0)
        cells.append(f"{b+s:7.1f}")
        hour_rank[d].append((b + s, h))
    print(f"  {h:02d} | " + " | ".join(cells))

# rank consistency: for each day, top-6 volume hours
print("\ntop-6 volume hours per day (UTC):")
for d in days:
    top = sorted(hour_rank[d], reverse=True)[:6]
    tot = sum(v for v, _ in hour_rank[d])
    print(f"  {d}: " + ", ".join(f"{h:02d}({100*v/max(tot,1e-9):.0f}%)" for v, h in top))

# aggregate hour profile
agg = collections.defaultdict(lambda: [0, 0.0, 0.0, 0])
for k, v in hs.items():
    if k[:10] < "2026-07-01": continue
    h = int(k[11:13])
    agg[h][0] += v[0]; agg[h][1] += v[1]; agg[h][2] += v[2]
for k, n in new_pools.items():
    if k[:10] >= "2026-07-01":
        agg[int(k[11:13])][3] += n
print("\naggregate (07-01..) by UTC hour: swaps | vol_eth | netflow_eth | new_pools")
rows = {}
for h in range(24):
    n, b, s, np_ = agg[h]
    rows[h] = {"swaps": n, "vol_eth": round(b+s, 1), "nf_eth": round(b-s, 1), "new_pools": np_}
    print(f"  {h:02d}: {n:7d} | {b+s:8.1f} | {b-s:+8.1f} | {np_:6d}")
json.dump({"days": days, "agg": rows,
           "per_day_top6": {d: [h for _, h in sorted(hour_rank[d], reverse=True)[:6]] for d in days}},
          open(os.path.join(OUT, "hour_rulebook.json"), "w"), indent=1)
print("\nwrote hour_rulebook.json")
