"""Resumable GT-first continuation: ohlc -> pool meta -> sol minute -> active io sweep.
Skips the dead recorder-tail remainder sweep. Re-run until 'phase3: ALL GT DONE'.
"""
import asyncio, json, os, sys, time

sys.path.insert(0, ".")
sys.path.insert(0, "scratchpad/ripday")
import harvest_driver as hd

DEADLINE = time.time() + 540

def left():
    return DEADLINE - time.time()

def main():
    hd.log("--- phase3 GT-first run ---")
    rip_sorted = sorted(hd.RIP.items(), key=lambda kv: -(kv[1].get("ts") or 0))
    todo = [(tok, r, r.get("ts") or 1782950400) for tok, r in rip_sorted
            if r.get("pair") and not os.path.exists(os.path.join(hd.OUT, "ohlc_%s.json" % tok[:8]))]
    todo += [(tok, hd.REC[tok], 1782849600) for tok in hd.CONTRAST
             if tok in hd.REC and hd.REC[tok].get("pair")
             and not os.path.exists(os.path.join(hd.OUT, "ohlc_%s.json" % tok[:8]))]
    hd.log("phase3: GT ohlc todo %d" % len(todo))
    done = 0
    for tok, r, ev in todo:
        if left() < 25:
            hd.log("phase3: budget out during ohlc (%d done, %d left)" % (done, len(todo) - done))
            return 3
        n = hd.gt_ohlc_for(tok, r["pair"], r.get("sym"), ev)
        done += 1
        if done % 15 == 0:
            hd.log("phase3: ohlc %d/%d" % (done, len(todo)))
    hd.log("phase3: GT ohlc done (%d)" % done)

    if not os.path.exists(os.path.join(hd.OUT, "token_meta.json")):
        if left() < 60: return 3
        pairs = [r["pair"] for _, r in hd.RIP.items() if r.get("pair")]
        pairs += [hd.REC[t]["pair"] for t in hd.CONTRAST if t in hd.REC and hd.REC[t].get("pair")]
        hd.log("phase3: pool meta")
        hd.gt_pool_meta(sorted(set(pairs)))

    if not os.path.exists(os.path.join(hd.OUT, "sol_usd_minute.json")):
        if left() < 90:
            hd.log("phase3: budget out before sol minute"); return 3
        hd.log("phase3: sol/usd minute history")
        hd.gt_sol_minute()

    hd.log("phase3: ALL GT DONE")
    act = hd.active_targets()
    n = min(len(act), max(0, int((left() - 15) / 2.6)))
    if n > 0:
        hd.log("phase3: active sweep %d/%d pairs" % (n, len(act)))
        asyncio.run(hd.sweep(act[:n], "sweepA"))
    hd.log("phase3: run complete")
    return 0

if __name__ == "__main__":
    sys.exit(main())
