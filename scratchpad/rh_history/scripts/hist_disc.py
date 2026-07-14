"""Stage 1: block-ts anchors + FULL pool discovery + ETH/USD price curve.
Writes: scratchpad/rh_history/{anchors.json, pools_registry.jsonl, eth_price_curve.json}
"""
import sys, time, json, os
sys.path.insert(0, r"C:\Users\jcole\multichain-bot")
from scripts.rh_chain_feed import (Rpc, RPC_DEFAULT, V3_FACTORY, V2_FACTORIES, WETH,
    TOPIC_POOL_CREATED, TOPIC_PAIR_CREATED, TOPIC_V3_SWAP,
    ETH_USD_POOL, LogRangeTimeout, parse_pool_created, parse_pair_created,
    sqrtprice_to_eth_usd, _word)

OUT = r"C:\Users\jcole\multichain-bot\scratchpad\rh_history"
os.makedirs(OUT, exist_ok=True)
rpc = Rpc(RPC_DEFAULT)

def logs_ranged(frm, to, addr, topics, chunk0=1_000_000, min_chunk=500, tag=""):
    """Adaptive-chunk eth_getLogs over [frm,to]. Halve on timeout / 10k-limit."""
    out, chunk, f = [], chunk0, frm
    fails = 0
    while f <= to:
        t = min(f + chunk - 1, to)
        try:
            logs = rpc.call("eth_getLogs", [{"fromBlock": hex(f), "toBlock": hex(t),
                            "address": addr, "topics": topics}], tries=1)
            out += logs
            f = t + 1
            fails = 0
            if chunk < chunk0:
                chunk = min(chunk0, chunk * 2)
            time.sleep(0.15)
        except (LogRangeTimeout, RuntimeError) as e:
            msg = str(e)
            if isinstance(e, LogRangeTimeout) or "exceeds limit" in msg:
                if chunk <= min_chunk:
                    print(f"[{tag}] SKIP {f}..{t} (unsplittable)", flush=True)
                    f = t + 1
                else:
                    chunk //= 2
                time.sleep(0.4)
            else:
                fails += 1
                if fails >= 4:
                    print(f"[{tag}] giving up window {f}..{t}: {msg[:120]}", flush=True)
                    f = t + 1
                    fails = 0
                time.sleep(1.5 * fails)
        except Exception as e:
            fails += 1
            time.sleep(3.0 * fails if "10054" in str(e) or "reset" in str(e).lower() else 1.2 * fails)
            if fails >= 5:
                print(f"[{tag}] giving up window {f}..{t}: {e}", flush=True)
                f = t + 1
                fails = 0
    return out

t_start = time.time()
head = rpc.call("eth_getBlockByNumber", ["latest", False])
HN, HTS = int(head["number"], 16), int(head["timestamp"], 16)
print(f"head={HN} ts={time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(HTS))}", flush=True)

# ---- anchors: every 25k blocks (finer near-now not needed; interp) ----
STEP = 25_000
want = list(range(1, HN, STEP)) + [HN]
anchors = []
res = rpc.batch([("eth_getBlockByNumber", [hex(b), False]) for b in want])
for i, b in enumerate(want):
    r = res.get(i)
    if r and r.get("timestamp"):
        anchors.append([b, int(r["timestamp"], 16)])
anchors.sort()
json.dump(anchors, open(os.path.join(OUT, "anchors.json"), "w"))
print(f"anchors: {len(anchors)}/{len(want)} in {time.time()-t_start:.0f}s", flush=True)

import bisect
A_B = [a[0] for a in anchors]
A_T = [a[1] for a in anchors]
def est_ts(block):
    i = bisect.bisect_right(A_B, block) - 1
    if i < 0: return A_T[0]
    if i >= len(A_B) - 1: return A_T[-1] + (block - A_B[-1]) * 0.1
    b0, t0, b1, t1 = A_B[i], A_T[i], A_B[i+1], A_T[i+1]
    return t0 + (t1 - t0) * (block - b0) / max(1, b1 - b0)

# ---- discovery: ALL creations block 1 -> head ----
t0 = time.time()
logs = logs_ranged(1, HN, [V3_FACTORY] + list(V2_FACTORIES),
                   [[TOPIC_POOL_CREATED, TOPIC_PAIR_CREATED]], tag="disc")
n_v3 = n_v2 = n_weth = 0
regf = open(os.path.join(OUT, "pools_registry.jsonl"), "w", encoding="utf-8")
seen = set()
for lg in logs:
    t = lg["topics"][0].lower()
    try:
        info = parse_pool_created(lg) if t == TOPIC_POOL_CREATED else parse_pair_created(lg)
    except Exception:
        continue
    if info["pool"] in seen:
        continue
    seen.add(info["pool"])
    if info["dex"] == "v3": n_v3 += 1
    else: n_v2 += 1
    wq = WETH in (info["token0"], info["token1"])
    if wq: n_weth += 1
    info["ts"] = int(est_ts(info["block"]))
    info["weth_quoted"] = wq
    regf.write(json.dumps(info, separators=(",", ":")) + "\n")
regf.close()
print(f"discovery: {len(seen)} pools (v3={n_v3} v2={n_v2} weth_quoted={n_weth}) "
      f"in {time.time()-t0:.0f}s", flush=True)

# pools per day
import collections
per_day = collections.Counter()
per_day_weth = collections.Counter()
for line in open(os.path.join(OUT, "pools_registry.jsonl"), encoding="utf-8"):
    d = json.loads(line)
    day = time.strftime("%Y-%m-%d", time.gmtime(d["ts"]))
    per_day[day] += 1
    if d["weth_quoted"]: per_day_weth[day] += 1
for day in sorted(per_day):
    print(f"  {day}: {per_day[day]:5d} pools ({per_day_weth[day]:5d} WETH-quoted)", flush=True)

# ---- ETH/USD curve from USDG-pool swap logs (event sqrtPrice; no state needed) ----
t0 = time.time()
curve = []
b = 1
while b < HN:
    win_logs = logs_ranged(b, min(b + 4000, HN), ETH_USD_POOL, [[TOPIC_V3_SWAP]],
                           chunk0=4001, min_chunk=500, tag="px")
    if win_logs:
        lg = win_logs[-1]
        try:
            sp = int(_word(lg["data"], 2), 16)
            p = sqrtprice_to_eth_usd(sp, True, 6)
            if 50 < p < 1_000_000:
                curve.append([int(lg["blockNumber"], 16), round(p, 2)])
        except Exception:
            pass
    b += 50_000
json.dump(curve, open(os.path.join(OUT, "eth_price_curve.json"), "w"))
if curve:
    lo = min(c[1] for c in curve); hi = max(c[1] for c in curve)
    print(f"price curve: {len(curve)} pts, eth ${lo:,.0f}..${hi:,.0f}, "
          f"first@blk {curve[0][0]} ({time.strftime('%m-%d', time.gmtime(est_ts(curve[0][0])))}) "
          f"in {time.time()-t0:.0f}s", flush=True)
print(f"total {time.time()-t_start:.0f}s | 429s={rpc.n_429} timeouts={rpc.n_timeout}", flush=True)
