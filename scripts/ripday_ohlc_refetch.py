#!/usr/bin/env python3
"""
ripday_ohlc_refetch.py — label the unlabeled tape trades (tape lens next-step).

The tapes in scratchpad/ripday/ (+live_tapes/) extend a median 841 minutes past
their OHLC bars, so thousands of captured trades have no price path to label
flush outcomes against. This fetches fresh GT minute bars covering each tape's
FULL span (+90m after, for bounce labeling) so the flush decode can rerun with
a real died class.

Single process, ~3s GT pacing, retry-on-429 (the proven pattern). Run:
    PYTHONPATH=. python scripts/ripday_ohlc_refetch.py
Output: scratchpad/ripday/ohlc2_{pair8}.json + refetch.log
"""
import glob
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RIP = os.path.join("scratchpad", "ripday")
PACING = 3.2
MAX_PAGES = 3   # 3 x 1000 minute bars = ~50h per pair, plenty


def log(msg):
    line = f"{time.strftime('%H:%M:%S', time.gmtime())} {msg}"
    print(line, flush=True)
    with open(os.path.join(RIP, "refetch.log"), "a") as f:
        f.write(line + "\n")


def gt_minute(pool, before_ts=None, retries=3):
    url = (f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}"
           f"/ohlcv/minute?aggregate=1&limit=1000")
    if before_ts:
        url += f"&before_timestamp={int(before_ts)}"
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            r = urllib.request.urlopen(req, timeout=20)
            d = json.loads(r.read())
            return ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(10 * (i + 1))
                continue
            return []
        except Exception:
            time.sleep(3)
    return []


def tape_span(path):
    lo = hi = None
    try:
        for ln in open(path, encoding="utf-8"):
            try:
                ts = json.loads(ln).get("ts")
                if not ts:
                    continue
                # ISO -> epoch
                import datetime
                t = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                lo = t if lo is None or t < lo else lo
                hi = t if hi is None or t > hi else hi
            except Exception:
                continue
    except Exception:
        pass
    return lo, hi


def main():
    tapes = {}
    for path in (glob.glob(os.path.join(RIP, "tape_*.jsonl"))
                 + glob.glob(os.path.join(RIP, "live_tapes", "tape_*.jsonl"))):
        # pair addr from first line
        try:
            first = json.loads(open(path, encoding="utf-8").readline())
            pair = first.get("pair")
            if pair:
                tapes.setdefault(pair, []).append(path)
        except Exception:
            continue
    log(f"refetch start: {len(tapes)} distinct pairs")
    done = skip = 0
    for pair, paths in tapes.items():
        out = os.path.join(RIP, f"ohlc2_{pair[:8]}.json")
        lo = hi = None
        for p in paths:
            l, h = tape_span(p)
            if l:
                lo = l if lo is None or l < lo else lo
            if h:
                hi = h if hi is None or h > hi else hi
        if lo is None:
            skip += 1
            continue
        need_from = lo - 3600          # 60m before tape start (flush context)
        need_to = hi + 5400            # 90m after tape end (bounce labeling)
        # skip if existing coverage is already sufficient
        if os.path.exists(out):
            try:
                ex = json.load(open(out))
                bars = ex.get("bars") or []
                if bars and bars[0][0] <= need_from and bars[-1][0] >= min(need_to, time.time() - 120):
                    skip += 1
                    continue
            except Exception:
                pass
        bars = []
        before = need_to
        for _ in range(MAX_PAGES):
            page = gt_minute(pair, before_ts=before)
            time.sleep(PACING)
            if not page:
                break
            bars = page + bars
            oldest = min(b[0] for b in page)
            if oldest <= need_from:
                break
            before = oldest
        # dedup + sort ascending
        seen = {}
        for b in bars:
            seen[b[0]] = b
        bars = [seen[k] for k in sorted(seen)]
        bars = [b for b in bars if need_from <= b[0] <= need_to + 3600]
        json.dump({"pair": pair, "n_bars": len(bars),
                   "span": [need_from, need_to], "bars": bars},
                  open(out, "w"))
        done += 1
        if done % 20 == 0:
            log(f"progress: {done} fetched, {skip} skipped")
    log(f"refetch complete: {done} fetched, {skip} skipped, {len(tapes)} pairs")


if __name__ == "__main__":
    main()
