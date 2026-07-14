#!/usr/bin/env python3
"""Fetch GT 1m bars covering each candidate tape's span; cache locally.
Usage: python scratchpad/_runner_bars.py [solana|robinhood]
"""
import json, glob, os, sys, time, urllib.request

NET = sys.argv[1] if len(sys.argv) > 1 else "solana"
TAPE_DIR = ("scratchpad/ripday/live_tapes" if NET == "solana"
            else "scratchpad/robinhood_tapes")
CACHE = "scratchpad/_runner_bars"
os.makedirs(CACHE, exist_ok=True)
GT = "https://api.geckoterminal.com/api/v2"
MIN_TRADES = 6900 if NET == "solana" else 580
# bot-traded labeled pairs (regular winners Jul 4-6) — always include
FORCE = {
    "solana": ["DtnW4aadcFK7jxK9D6fHWzSGEKZeLavWn8oXuEJe6nTc",
               "FvTXwZTVGsDCnmNmAz8hFwCCP4WyDZYyg8zRQpAoB4M9",
               "HKSDMJ6KThscZXMVVuS24wvK3WGzfuepQhwdKCjHKEpZ",
               "61k5vCzCbDxPDD8EvK6kQ4eFDGxC7ktrnEYB4zWeTTSX",
               "B1GFLecDJbYgDDSkKQMDp8xz5TTiCgdW7ADDVqqSUUFe",
               "LjvUBiEWWjxbShc6DBLnAiNupZuPahUwH1YChdB8yKX",
               "4s4cAwsgMZQPm7HnwHadwUYCw9B3dMZQKVVcqm4Qai53"],
    "robinhood": ["0x733d40245c6f4baace8860d8e7d670b7eddecc36",  # CASHCOW
                  "0x9a407531e327add09009d4c5838725b7bebb9225",  # KITTY
                  "0x9e9038860b777b977eb08421eeaf99bc8673bacf",  # RANGER
                  "0xd60990c1d9b9612d0e7b7351d83e36a0356e3b20",  # BILLY
                  "0xb734e41ce2cbfd4bd6d9c35c68d73e4c1b3de6a6"], # THROBBIN
}[NET]

def gt(path):
    req = urllib.request.Request(GT + path, headers={
        "User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"  429, backoff {20*(attempt+1)}s", flush=True)
                time.sleep(20 * (attempt + 1))
                continue
            raise
    return None

def iso2unix(s):
    from datetime import datetime, timezone
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())

# 1. candidates: tapes with enough trades
cands = []
for p in glob.glob(os.path.join(TAPE_DIR, "tape_*.jsonl")):
    n = 0; ts0 = None; ts1 = None; sym = "?"; pair = None
    for l in open(p, encoding="utf-8"):
        try: t = json.loads(l)
        except: continue
        n += 1
        sym = t.get("sym") or sym; pair = t.get("pair") or pair
        ts = t.get("ts")
        if ts0 is None or ts < ts0: ts0 = ts
        if ts1 is None or ts > ts1: ts1 = ts
    if pair and (n >= MIN_TRADES or pair[:8] in {f[:8] for f in FORCE}):
        cands.append({"pair": pair, "sym": sym, "n": n,
                      "t0": iso2unix(ts0), "t1": iso2unix(ts1), "file": p})
cands.sort(key=lambda c: -c["n"])
print(f"{len(cands)} candidate tapes (n>={MIN_TRADES})")

# 2. fetch bars per candidate, paginating back until tape start covered
for i, c in enumerate(cands):
    out = os.path.join(CACHE, f"bars_{NET}_{c['pair'][:10]}.json")
    if os.path.exists(out):
        print(f"[{i+1}/{len(cands)}] {c['sym']}: cached"); continue
    bars = {}
    before = c["t1"] + 3600  # a bit past tape end
    pages = 0
    while pages < 8:
        d = gt(f"/networks/{NET}/pools/{c['pair']}/ohlcv/minute"
               f"?aggregate=1&limit=1000&before_timestamp={before}&currency=usd")
        pages += 1
        time.sleep(3.2)
        lst = (((d or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        if not lst:
            break
        for row in lst:
            bars[int(row[0])] = row
        oldest = min(int(r[0]) for r in lst)
        if oldest <= c["t0"] - 600 or len(lst) < 1000:
            break
        before = oldest
    blist = sorted(bars.values(), key=lambda r: r[0])
    json.dump({"pair": c["pair"], "sym": c["sym"], "net": NET,
               "tape_t0": c["t0"], "tape_t1": c["t1"], "n_tape": c["n"],
               "bars": blist}, open(out, "w"))
    cov = "?"
    if blist:
        cov = f"{time.strftime('%m-%d %H:%M', time.gmtime(blist[0][0]))}..{time.strftime('%m-%d %H:%M', time.gmtime(blist[-1][0]))}"
    print(f"[{i+1}/{len(cands)}] {c['sym']}: {len(blist)} bars pages={pages} cov={cov}", flush=True)
print("done")
