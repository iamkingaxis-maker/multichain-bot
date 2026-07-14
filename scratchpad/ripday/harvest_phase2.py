"""Resumable continuation of harvest_driver: finishes remaining work, skips done.
Safe to re-run; each run does at most ~9 min of work then exits (rc 3 = more work left).
"""
import asyncio, json, os, sys, time

sys.path.insert(0, ".")
sys.path.insert(0, "scratchpad/ripday")
import harvest_driver as hd

DEADLINE = time.time() + 540  # ~9 min budget per run

def left():
    return DEADLINE - time.time()

def main():
    hd.log("--- phase2 continuation run ---")
    more = False

    # 1. finish sweep1 remainder (pairs never swept)
    targets = [t for t in hd.all_targets() if t[1] not in hd.INDEX]
    if targets:
        n = min(len(targets), max(0, int(left() / 2.6)))
        hd.log("phase2: sweep1 remainder %d pairs (doing %d)" % (len(targets), n))
        if n:
            asyncio.run(hd.sweep(targets[:n], "sweep1r"))
        if n < len(targets):
            hd.log("phase2: budget out during sweep1r"); return 3

    # 2. GT ohlc (cached files skipped inside gt_ohlc_for)
    rip_sorted = sorted(hd.RIP.items(), key=lambda kv: -(kv[1].get("ts") or 0))
    todo = [(tok, r) for tok, r in rip_sorted
            if r.get("pair") and not os.path.exists(os.path.join(hd.OUT, "ohlc_%s.json" % tok[:8]))]
    ctodo = [(tok, hd.REC[tok]) for tok in hd.CONTRAST
             if tok in hd.REC and hd.REC[tok].get("pair")
             and not os.path.exists(os.path.join(hd.OUT, "ohlc_%s.json" % tok[:8]))]
    if todo or ctodo:
        hd.log("phase2: GT ohlc todo %d rip + %d contrast" % (len(todo), len(ctodo)))
        for tok, r in todo:
            if left() < 30: hd.log("phase2: budget out during ohlc"); return 3
            hd.gt_ohlc_for(tok, r["pair"], r.get("sym"), r.get("ts") or 1782950400)
        for tok, r in ctodo:
            if left() < 30: hd.log("phase2: budget out during ohlc"); return 3
            hd.gt_ohlc_for(tok, r["pair"], r.get("sym"), 1782849600)
        hd.log("phase2: GT ohlc done")

    # 3. pool meta
    if not os.path.exists(os.path.join(hd.OUT, "token_meta.json")):
        if left() < 60: return 3
        pairs = [r["pair"] for _, r in hd.RIP.items() if r.get("pair")]
        pairs += [hd.REC[t]["pair"] for t in hd.CONTRAST if t in hd.REC and hd.REC[t].get("pair")]
        hd.log("phase2: pool meta")
        hd.gt_pool_meta(sorted(set(pairs)))

    # 4. sol minute history
    if not os.path.exists(os.path.join(hd.OUT, "sol_usd_minute.json")):
        if left() < 120:
            hd.log("phase2: budget out before sol minute"); return 3
        hd.log("phase2: sol/usd minute history")
        hd.gt_sol_minute()

    # 5. one active re-sweep with remaining budget
    act = hd.active_targets()
    n = min(len(act), max(0, int((left() - 15) / 2.6)))
    if n > 0:
        hd.log("phase2: active sweep %d/%d pairs" % (n, len(act)))
        asyncio.run(hd.sweep(act[:n], "sweepA"))
    hd.log("phase2: run complete (all core work done)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
