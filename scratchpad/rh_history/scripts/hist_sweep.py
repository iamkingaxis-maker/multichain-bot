"""Stage 2: FULL chain-wide swap sweep (all V3+V2 swap logs, no address filter).
Streams compact decoded rows to sweep_logs.jsonl.gz, accumulates exact per-pool
counts/volumes + per-hour chain stats. Resume-safe via sweep_state.json.
Writes: rh_history/{sweep_logs.jsonl.gz (append), sweep_counts.json, sweep_state.json}
"""
import sys, time, json, os, gzip, collections, bisect
sys.path.insert(0, r"C:\Users\jcole\multichain-bot")
from scripts.rh_chain_feed import (Rpc, RPC_DEFAULT, WETH,
    TOPIC_V3_SWAP, TOPIC_V2_SWAP, LogRangeTimeout, _word, _s256)

OUT = r"C:\Users\jcole\multichain-bot\scratchpad\rh_history"
rpc = Rpc(RPC_DEFAULT)

# registry: pool -> (weth0, is_weth_quoted, dex)
reg = {}
min_block = None
for line in open(os.path.join(OUT, "pools_registry.jsonl"), encoding="utf-8"):
    d = json.loads(line)
    reg[d["pool"]] = (d["token0"] == WETH, d["weth_quoted"], d["dex"])
    if min_block is None or d["block"] < min_block:
        min_block = d["block"]

anchors = json.load(open(os.path.join(OUT, "anchors.json")))
A_B = [a[0] for a in anchors]; A_T = [a[1] for a in anchors]
def est_ts(block):
    i = bisect.bisect_right(A_B, block) - 1
    if i < 0: return A_T[0]
    if i >= len(A_B) - 1: return A_T[-1] + (block - A_B[-1]) * 0.1
    b0, t0, b1, t1 = A_B[i], A_T[i], A_B[i+1], A_T[i+1]
    return t0 + (t1 - t0) * (block - b0) / max(1, b1 - b0)

head = rpc.call("eth_getBlockByNumber", ["latest", False])
HN = int(head["number"], 16)

state_path = os.path.join(OUT, "sweep_state.json")
counts_path = os.path.join(OUT, "sweep_counts.json")
if os.path.exists(state_path):
    st = json.load(open(state_path))
    start = st["next_block"]
    C = json.load(open(counts_path))
    pool_stats = {p: v for p, v in C["pool_stats"].items()}
    hour_stats = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0],
                 {k: v for k, v in C["hour_stats"].items()})
    n_total = C["n_total"]; n_unknown = C["n_unknown"]; n_nonweth = C["n_nonweth"]
    print(f"RESUME from block {start}", flush=True)
else:
    start = min_block
    pool_stats = {}   # pool -> [n_swaps, buy_wei, sell_wei, first_blk, last_blk]
    hour_stats = collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0])  # "YYYY-MM-DDTHH" -> [n, buy_eth, sell_eth, _]
    n_total = n_unknown = n_nonweth = 0

gz = gzip.open(os.path.join(OUT, "sweep_logs.jsonl.gz"), "at", encoding="utf-8")

def decode_row(lg):
    """-> (pool, kind, weth_wei, px_weth_rel, block, tx, logIndex) | None"""
    pool = lg["address"].lower()
    r = reg.get(pool)
    if r is None:
        return "unknown"
    weth0, wq, dex = r
    if not wq:
        return "nonweth"
    t0 = lg["topics"][0].lower()
    data = lg["data"]
    try:
        if t0 == TOPIC_V3_SWAP:
            a0 = _s256(_word(data, 0)); a1 = _s256(_word(data, 1))
            wd = a0 if weth0 else a1
            td = a1 if weth0 else a0
            if wd == 0: return None
            kind = "buy" if wd > 0 else "sell"
            weth_wei = abs(wd)
            sp = int(_word(data, 2), 16)
            raw = (sp / 2 ** 96) ** 2  # token1/token0 atomic
            px = (1.0 / raw) if weth0 else raw  # token price in WETH (rel)
        else:
            a0i = int(_word(data, 0), 16); a1i = int(_word(data, 1), 16)
            a0o = int(_word(data, 2), 16); a1o = int(_word(data, 3), 16)
            wnet = (a0i - a0o) if weth0 else (a1i - a1o)
            tnet = (a1i - a1o) if weth0 else (a0i - a0o)
            if wnet == 0: return None
            kind = "buy" if wnet > 0 else "sell"
            weth_wei = abs(wnet)
            px = abs(wnet) / abs(tnet) if tnet != 0 else 0.0
        return (pool, kind, weth_wei, px, int(lg["blockNumber"], 16),
                str(lg["transactionHash"]).lower(),
                int(lg["logIndex"], 16) if isinstance(lg["logIndex"], str) else int(lg["logIndex"] or 0))
    except Exception:
        return None

CHUNK0, CHUNK_MAX, CHUNK_MIN = 50_000, 200_000, 200
chunk = CHUNK0
f = start
t_start = time.time()
n_win = 0
last_save = time.time()
while f <= HN:
    to = min(f + chunk - 1, HN)
    try:
        logs = rpc.call("eth_getLogs", [{"fromBlock": hex(f), "toBlock": hex(to),
                        "topics": [[TOPIC_V3_SWAP, TOPIC_V2_SWAP]]}], tries=1)
    except (LogRangeTimeout, RuntimeError) as e:
        if isinstance(e, LogRangeTimeout) or "exceeds limit" in str(e):
            if chunk <= CHUNK_MIN:
                print(f"[sweep] SKIP dense window {f}..{to}", flush=True)
                f = to + 1
            else:
                chunk = max(CHUNK_MIN, chunk // 2)
            time.sleep(0.3)
            continue
        time.sleep(2.0)
        continue
    except Exception as e:
        time.sleep(4.0 if "10054" in str(e) or "reset" in str(e).lower() else 1.5)
        continue
    n_win += 1
    for lg in logs:
        n_total += 1
        row = decode_row(lg)
        if row == "unknown":
            n_unknown += 1; continue
        if row == "nonweth":
            n_nonweth += 1; continue
        if row is None:
            continue
        pool, kind, wei, px, blk, tx, li = row
        ps = pool_stats.setdefault(pool, [0, 0.0, 0.0, blk, blk])
        ps[0] += 1
        eth = wei / 1e18
        if kind == "buy": ps[1] += eth
        else: ps[2] += eth
        ps[3] = min(ps[3], blk); ps[4] = max(ps[4], blk)
        hh = time.strftime("%Y-%m-%dT%H", time.gmtime(est_ts(blk)))
        h = hour_stats[hh]
        h[0] += 1
        if kind == "buy": h[1] += eth
        else: h[2] += eth
        gz.write(json.dumps({"p": pool, "k": kind, "w": round(eth, 8), "px": px,
                             "b": blk, "x": tx, "i": li}, separators=(",", ":")) + "\n")
    f = to + 1
    if len(logs) < 3000 and chunk < CHUNK_MAX:
        chunk = min(CHUNK_MAX, int(chunk * 1.5))
    time.sleep(0.12)
    if n_win % 25 == 0 or time.time() - last_save > 60:
        gz.flush()
        json.dump({"pool_stats": pool_stats, "hour_stats": dict(hour_stats),
                   "n_total": n_total, "n_unknown": n_unknown,
                   "n_nonweth": n_nonweth}, open(counts_path, "w"))
        json.dump({"next_block": f}, open(state_path, "w"))
        last_save = time.time()
        pct = 100.0 * (f - min_block) / max(1, HN - min_block)
        print(f"[sweep] blk {f}/{HN} ({pct:.1f}%) logs={n_total} pools={len(pool_stats)} "
              f"chunk={chunk} 429s={rpc.n_429} elapsed={time.time()-t_start:.0f}s", flush=True)

gz.close()
json.dump({"pool_stats": pool_stats, "hour_stats": dict(hour_stats),
           "n_total": n_total, "n_unknown": n_unknown,
           "n_nonweth": n_nonweth}, open(counts_path, "w"))
json.dump({"next_block": HN + 1, "done": True, "head": HN}, open(state_path, "w"))
print(f"[sweep] DONE: {n_total} swap logs ({n_unknown} unknown-pool, {n_nonweth} non-WETH) "
      f"across {len(pool_stats)} WETH pools in {time.time()-t_start:.0f}s", flush=True)
