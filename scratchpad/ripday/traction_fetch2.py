# traction_fetch2.py -- resume + extend (single process)
# 1. new_pools refresh (window-4, additive for future reruns + rate point)
# 2. finish window-1 bars (all pools, no screen)
# 3. DS state refresh for all resolved windows (w1+w2+w3)
# 4. w2/w3: fetch bars for pools with ANY life signal (DS vol24>=1k or
#    liq>=5k or birth reserve>=10k or vol_h1_seen>=1k); rest = dead
#    negatives (vol-threshold features decidable-fail without bars)
# 5. recall set (fixed recorder keys), 40 pools
import json, os, sys, time
from datetime import datetime, timezone

RIP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RIP)
from traction_fetch import (gt_get, iso_to_epoch, now_utc, log, PACE,
                            refresh_newpools, build_recall_set)

W4_CUTOFF = datetime(2026, 7, 3, 7, 0, tzinfo=timezone.utc).timestamp()

def fetch_bars_for(addrs, tag):
    bdir = os.path.join(RIP, "_gt_bars"); os.makedirs(bdir, exist_ok=True)
    todo = [a for a in addrs if not os.path.exists(os.path.join(bdir, a[:12] + ".json"))]
    log("%s: %d to fetch" % (tag, len(todo)))
    for i, addr in enumerate(todo):
        j = gt_get("https://api.geckoterminal.com/api/v2/networks/solana/pools/%s/ohlcv/minute"
                   % addr, {"aggregate": 1, "limit": 1000, "currency": "usd"})
        time.sleep(PACE)
        f = os.path.join(bdir, addr[:12] + ".json")
        try: raw = j["data"]["attributes"]["ohlcv_list"]
        except Exception:
            json.dump([], open(f, "w")); continue
        bars = sorted([[int(b[0]), b[1], b[2], b[3], b[4], float(b[5])] for b in raw],
                      key=lambda b: b[0])
        json.dump(bars, open(f, "w"))
        if (i + 1) % 20 == 0: log("  %d/%d" % (i + 1, len(todo)))
    log("%s done" % tag)

def ds_refresh(addrs):
    """Refresh DS state for addrs (force: everything older than 2h)."""
    cache_f = os.path.join(RIP, "_ds_state_cache.json")
    cache = json.load(open(cache_f)) if os.path.exists(cache_f) else {}
    nowt = now_utc()
    missing = [a for a in addrs if a not in cache or nowt - cache[a].get("_at", 0) > 7200]
    log("DS refresh: %d pools" % len(missing))
    import requests
    UA = {"User-Agent": "Mozilla/5.0 (research; traction-predictor-study)"}
    for i in range(0, len(missing), 30):
        batch = missing[i:i + 30]
        try:
            r = requests.get("https://api.dexscreener.com/latest/dex/pairs/solana/"
                             + ",".join(batch), headers=UA, timeout=25)
            got = set()
            if r.status_code == 200:
                for pr in (r.json() or {}).get("pairs") or []:
                    pa = pr.get("pairAddress")
                    if not pa: continue
                    got.add(pa)
                    liqd = pr.get("liquidity") or {}
                    cache[pa] = {"_at": nowt,
                                 "liq": float(liqd.get("usd") or 0),
                                 "mcap": float(pr.get("marketCap") or 0),
                                 "fdv": float(pr.get("fdv") or 0),
                                 "price": float(pr.get("priceUsd") or 0),
                                 "vol24": float((pr.get("volume") or {}).get("h24") or 0),
                                 "token": (pr.get("baseToken") or {}).get("address"),
                                 "sym": (pr.get("baseToken") or {}).get("symbol")}
            for a in batch:
                if a not in got and a not in cache:
                    cache[a] = {"_at": nowt, "delisted": True}
        except Exception as e:
            log("  ds err %r" % e)
        time.sleep(1.2)
    json.dump(cache, open(cache_f, "w"))
    return cache

if __name__ == "__main__":
    pools = refresh_newpools()   # window-4 additive
    nowt = now_utc()
    resolved = {}
    for addr, p in pools.items():
        c = iso_to_epoch(p.get("created"))
        if c is None or c >= W4_CUTOFF: continue
        if (nowt - c) / 3600.0 < 6.0: continue
        resolved[addr] = (c, p)
    log("resolved cohort (w1+w2+w3): %d pools" % len(resolved))
    w1 = [a for a, (c, p) in resolved.items()
          if c < datetime(2026, 7, 3, 0, 0, tzinfo=timezone.utc).timestamp()]
    w23 = [a for a in resolved if a not in set(w1)]
    fetch_bars_for(w1, "w1 remainder")
    ds = ds_refresh(list(resolved))
    alive = []
    for a in w23:
        c, p = resolved[a]
        s = ds.get(a, {})
        if (s.get("vol24", 0) or 0) >= 1000 or (s.get("liq", 0) or 0) >= 5000 \
           or p.get("reserve", 0) >= 10000 or p.get("vol_h1", 0) >= 1000:
            alive.append(a)
    log("w2/w3: %d pools, %d alive-screened for bars" % (len(w23), len(alive)))
    json.dump({"w23_dead": [a for a in w23 if a not in set(alive)]},
              open(os.path.join(RIP, "_w23_dead.json"), "w"))
    fetch_bars_for(alive, "w2/w3 alive")
    build_recall_set(max_pools=40)
    log("ALL DONE")
