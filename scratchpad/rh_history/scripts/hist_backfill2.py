"""Stage 3 v2: day-balanced target selection + maker resolution (20 blk/s reality).

Tiers: (A) lane pools, (B) top leaders 5k<swaps<=20k by volume (max 6),
(C) mid-tier 200<=swaps<=5000, round-robin across pool-creation days by volume.
Union block budget + wall-clock cap; resume-safe (skips existing hist_ files);
missing blocks are retried until resolved (up to 6 rounds).

Usage: python hist_backfill2.py [block_budget] [max_minutes]
"""
import sys, time, json, os, gzip, collections, bisect
sys.path.insert(0, r"C:\Users\jcole\multichain-bot")
import scripts.rh_chain_feed as F
from scripts.rh_chain_feed import (Rpc, RPC_DEFAULT, WETH, ETH_USD_POOL,
    SEL_SYMBOL, decode_symbol, iso_utc)

F.BATCH_CHUNK = 40
F.BATCH_PACE_S = 0.4

OUT = r"C:\Users\jcole\multichain-bot\scratchpad\rh_history"
BLOCK_BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 190_000
MAX_MINUTES = float(sys.argv[2]) if len(sys.argv) > 2 else 170.0
rpc = Rpc(RPC_DEFAULT)

reg = {}
for line in open(os.path.join(OUT, "pools_registry.jsonl"), encoding="utf-8"):
    d = json.loads(line)
    reg[d["pool"]] = d

C = json.load(open(os.path.join(OUT, "sweep_counts.json")))
pool_stats = C["pool_stats"]
lane = [p for p in json.load(open(os.path.join(OUT, "lane_pools.json"))) if p in pool_stats]

stable_tokens = set()
e = reg.get(ETH_USD_POOL.lower())
if e:
    stable_tokens.add(e["token0"] if e["token1"] == WETH else e["token1"])
def is_utility(pool):
    r = reg.get(pool)
    if not r: return True
    tok = r["token1"] if r["token0"] == WETH else r["token0"]
    return tok in stable_tokens

def vol(p): return pool_stats[p][1] + pool_stats[p][2]
def nsw(p): return pool_stats[p][0]

leaders = [p for p in sorted(pool_stats, key=lambda q: -vol(q))
           if 5000 < nsw(p) <= 20000 and not is_utility(p) and p not in lane][:3]

# breadth over depth: small pools maximize pools-per-block (wallet decode
# needs cross-pool maker visibility, not raw volume coverage)
mid = [p for p in pool_stats
       if 200 <= nsw(p) <= 1500 and not is_utility(p)
       and p not in lane and p not in leaders]
by_day = collections.defaultdict(list)
for p in mid:
    day = time.strftime("%Y-%m-%d", time.gmtime(reg[p]["ts"])) if p in reg else "?"
    by_day[day].append(p)
for d in by_day:
    by_day[d].sort(key=lambda q: -vol(q))
days = sorted(by_day)

# ---- pass A: rows for candidate superset (lane + leaders + top ~80/day) ----
cand = set(lane) | set(leaders)
for d in days:
    cand |= set(by_day[d][:220])
t0 = time.time()
pend = collections.defaultdict(list)
with gzip.open(os.path.join(OUT, "sweep_logs.jsonl.gz"), "rt", encoding="utf-8") as f:
    for ln in f:
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if d["p"] in cand:
            pend[d["p"]].append((d["k"], d["w"], d["px"], d["b"], d["x"], d["i"]))
for p in pend:
    seen, uniq = set(), []
    for r in pend[p]:
        k = (r[4], r[5])
        if k in seen: continue
        seen.add(k); uniq.append(r)
    pend[p] = uniq
print(f"[bf2] pass A: {sum(len(v) for v in pend.values())} rows for "
      f"{len(pend)} candidate pools in {time.time()-t0:.0f}s", flush=True)

pool_blocks = {p: {r[3] for r in pend[p]} for p in pend}

# ---- selection under union budget ----
union = set()
order = []
def take(p):
    global union
    pb = pool_blocks.get(p)
    if pb is None: return False
    if len(union | pb) > BLOCK_BUDGET: return False
    union |= pb
    order.append(p)
    return True

for p in lane: take(p)
n_lane = len(order)
for p in leaders: take(p)
n_lead = len(order) - n_lane
# round-robin across days by volume rank
idx = {d: 0 for d in days}
active = set(days)
while active:
    for d in list(days):
        if d not in active: continue
        lst = by_day[d]
        i = idx[d]
        while i < len(lst) and (lst[i] not in pool_blocks or not take(lst[i])):
            i += 1
            if lst[i-1] in pool_blocks and len(union) >= BLOCK_BUDGET * 0.995:
                break
        idx[d] = i + 1
        if idx[d] >= len(lst) or len(union) >= BLOCK_BUDGET * 0.995:
            active.discard(d)
    if len(union) >= BLOCK_BUDGET * 0.995:
        break
n_mid = len(order) - n_lane - n_lead
# PROCESSING order: mid smalls first (fast, breadth = decode value), lane next
# (v0 recorder tapes already cover lane pools on 07-10), leaders last (each is
# ~1h of blocks; only reached if time allows)
lane_set, lead_set = set(lane), set(leaders)
order = ([p for p in order if p not in lane_set and p not in lead_set]
         + [p for p in order if p in lane_set]
         + [p for p in order if p in lead_set])
day_counts = collections.Counter(
    time.strftime("%m-%d", time.gmtime(reg[p]["ts"])) for p in order if p in reg)
print(f"[bf2] selected {len(order)} pools (lane={n_lane} leaders={n_lead} mid={n_mid}) "
      f"union_blocks={len(union)}", flush=True)
print(f"[bf2] by creation day: {dict(sorted(day_counts.items()))}", flush=True)
tot_vol = sum(v[1] + v[2] for v in pool_stats.values())
sel_vol = sum(vol(p) for p in order)
sel_n = sum(nsw(p) for p in order)
tot_n = sum(v[0] for v in pool_stats.values())
print(f"[bf2] coverage: {100*sel_vol/tot_vol:.1f}% WETH vol, {sel_n} swaps "
      f"({100*sel_n/tot_n:.1f}%)", flush=True)

# ---- symbols ----
sym_map = {}
toks = [(p, (reg[p]["token1"] if reg[p]["token0"] == WETH else reg[p]["token0"])
         if p in reg else None) for p in order]
res = rpc.batch([("eth_call", [{"to": t, "data": SEL_SYMBOL}, "latest"])
                 for p, t in toks if t])
i = 0
for p, t in toks:
    if not t:
        sym_map[p] = "?"; continue
    sym_map[p] = decode_symbol(res.get(i) or "0x")
    i += 1

# ---- ETH/USD + ts helpers ----
curve = json.load(open(os.path.join(OUT, "eth_price_curve.json")))
daily = json.load(open(os.path.join(OUT, "eth_daily_usd.json")))
anchors = json.load(open(os.path.join(OUT, "anchors.json")))
A_B = [a[0] for a in anchors]; A_T = [a[1] for a in anchors]
def est_ts(block):
    i = bisect.bisect_right(A_B, block) - 1
    if i < 0: return A_T[0]
    if i >= len(A_B) - 1: return A_T[-1] + (block - A_B[-1]) * 0.1
    b0, t0_, b1, t1 = A_B[i], A_T[i], A_B[i+1], A_T[i+1]
    return t0_ + (t1 - t0_) * (block - b0) / max(1, b1 - b0)
C_B = [c[0] for c in curve]; C_P = [c[1] for c in curve]
def eth_usd(block):
    if C_B and block >= C_B[0]:
        i = min(bisect.bisect_right(C_B, block) - 1, len(C_B) - 1)
        return C_P[max(0, i)]
    day = time.strftime("%Y-%m-%d", time.gmtime(est_ts(block)))
    return daily.get(day) or (C_P[0] if C_P else 1750.0)

# ---- pass B: maker resolution ----
BLK_CACHE = {}
t0 = time.time()
t_stop = t0 + MAX_MINUTES * 60
n_done = n_skip = blocks_fetched = 0
total_missing = 0
for pi, pool in enumerate(order):
    outp = os.path.join(OUT, f"hist_{pool[:12]}.jsonl")
    if os.path.exists(outp):
        n_skip += 1
        continue
    if time.time() > t_stop:
        print(f"[bf2] WALL-CLOCK STOP after {n_done} pools "
              f"({len(order)-pi} remaining, resume-safe)", flush=True)
        break
    rows = sorted(pend[pool], key=lambda r: (r[3], r[5]))
    need = sorted({r[3] for r in rows if r[3] not in BLK_CACHE})
    rounds = 0
    while need and rounds < 6:
        got_any = False
        for ofs in range(0, len(need), 200):
            grp = need[ofs:ofs + 200]
            res = rpc.batch([("eth_getBlockByNumber", [hex(b), True]) for b in grp])
            for j, b in enumerate(grp):
                r = res.get(j)
                if not r: continue
                txf = {}
                for tx in r.get("transactions") or []:
                    if isinstance(tx, dict) and tx.get("hash") and tx.get("from"):
                        txf[str(tx["hash"]).lower()] = tx["from"].lower()
                BLK_CACHE[b] = (int(r["timestamp"], 16), txf)
                got_any = True
            blocks_fetched += sum(1 for j in range(len(grp)) if res.get(j))
            time.sleep(0.2)
        need = [b for b in need if b not in BLK_CACHE]
        rounds += 1
        if need:
            time.sleep(2.0 * rounds if not got_any else 0.5)
    total_missing += len(need)
    if len(BLK_CACHE) > 120_000:
        for b in sorted(BLK_CACHE)[:60_000]:
            del BLK_CACHE[b]
    sym = sym_map.get(pool, "?")
    px_usd_cache = {}
    n_mm = 0
    tmp = outp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for (k_, w_, px_, b, x_, i_) in rows:
            ts, txf = BLK_CACHE.get(b, (int(est_ts(b)), {}))
            maker = txf.get(x_, "")
            if not maker: n_mm += 1
            pu = px_usd_cache.get(b)
            if pu is None:
                pu = eth_usd(b); px_usd_cache[b] = pu
            f.write(json.dumps({
                "kind": k_, "volume_usd": round(w_ * pu, 2),
                "ts": iso_utc(ts), "maker": maker, "pair": pool, "sym": sym,
                "px": px_, "block": b, "x": x_}, separators=(",", ":")) + "\n")
    os.replace(tmp, outp)
    n_done += 1
    if n_mm > 0.02 * len(rows):
        print(f"[bf2] WARN {sym} {pool[:10]}: {n_mm}/{len(rows)} maker-less", flush=True)
    if n_done % 10 == 0:
        rate = blocks_fetched / max(1e-9, time.time() - t0)
        eta = (len(union) - blocks_fetched) / max(rate, 1e-9) / 60
        print(f"[bf2] {n_done} pools done ({pi+1}/{len(order)} walked) | blocks "
              f"{blocks_fetched}/{len(union)} ({rate:.0f}/s, eta {eta:.0f}m) | "
              f"429s={rpc.n_429} | {(time.time()-t0)/60:.0f}m", flush=True)

json.dump({"selected": order, "n_lane": n_lane, "n_leaders": n_lead, "n_mid": n_mid,
           "union_blocks": len(union),
           "coverage_vol_pct": round(100*sel_vol/tot_vol, 2),
           "coverage_swaps_pct": round(100*sel_n/tot_n, 2),
           "tot_pools_with_swaps": len(pool_stats), "tot_swaps": tot_n,
           "tot_vol_eth": round(tot_vol, 1), "by_day": dict(day_counts),
           "n_done_this_run": n_done, "total_missing_blocks": total_missing},
          open(os.path.join(OUT, "backfill_manifest.json"), "w"), indent=1)
print(f"[bf2] DONE: {n_done} pools written ({n_skip} pre-existing), "
      f"{blocks_fetched} blocks, {total_missing} unresolved blocks, "
      f"{(time.time()-t0)/60:.0f}m", flush=True)
