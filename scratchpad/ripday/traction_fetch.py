# traction_fetch.py -- BIRTH-MINUTE TRACTION PREDICTOR, data pull (single process)
# (a) GT new_pools refresh (additive window-3 for future reruns)
# (b) birth bars for ALL window-1 cohort pools not yet in _gt_bars/
#     (pools ~11h old; limit=1000 minute bars reaches birth since <660 traded
#      minutes possible)
# (c) positive-enriched recall set: dashboard universe-recorder pools that
#     reached the floor (mcap>=100k & liq>=25k) while YOUNG (age@event<=24h)
#     -> GT pools/multi for pool_created_at -> birth-window bars via
#     before_timestamp=created+3600.
# Caches: _gt_bars/ (window-1), _gt_bars_b/ (recall set), _recall_set.json.
# ASCII only. ~3.2s GT pacing, retry-429.

import gzip, io, json, os, sys, time, urllib.request
from datetime import datetime, timezone

RIP = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (research; traction-predictor-study)"}
PACE = 3.2
DASH = "https://gracious-inspiration-production.up.railway.app"

def log(m):
    print("%s %s" % (datetime.now(timezone.utc).strftime("%H:%M:%S"), m), flush=True)

def now_utc(): return datetime.now(timezone.utc).timestamp()

def iso_to_epoch(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception: return None

def gt_get(url, params=None):
    from curl_cffi import requests as cr
    for attempt in range(4):
        try:
            r = cr.get(url, params=params, headers=UA, impersonate="chrome", timeout=25)
            if r.status_code == 429:
                log("  429, backoff %ds" % (12 * (attempt + 1)))
                time.sleep(12 * (attempt + 1)); continue
            if r.status_code == 200: return r.json()
            return None
        except Exception as e:
            log("  err %r" % e); time.sleep(5)
    return None

# ---------- (a) new_pools refresh ----------
def refresh_newpools():
    cache_f = os.path.join(RIP, "_gt_newpools_cache.json")
    cache = json.load(open(cache_f))
    added = 0
    for pg in range(1, 11):
        j = gt_get("https://api.geckoterminal.com/api/v2/networks/solana/new_pools",
                   {"page": pg})
        time.sleep(PACE)
        if not j or "data" not in j: break
        for d in j["data"]:
            a = d.get("attributes", {})
            addr = a.get("address")
            if not addr or addr in cache["pools"]: continue
            vol = a.get("volume_usd") or {}
            cache["pools"][addr] = {
                "created": a.get("pool_created_at"),
                "reserve": float(a.get("reserve_in_usd") or 0),
                "vol_h1": float(vol.get("h1") or 0),
                "vol_h24": float(vol.get("h24") or 0),
                "name": a.get("name"), "seen": now_utc()}
            added += 1
    cache["fetched"].append(now_utc())
    json.dump(cache, open(cache_f, "w"))
    log("(a) new_pools refresh: +%d (cache %d)" % (added, len(cache["pools"])))
    return cache["pools"]

# ---------- (b) window-1 cohort bars ----------
def fetch_cohort_bars(pools):
    bdir = os.path.join(RIP, "_gt_bars"); os.makedirs(bdir, exist_ok=True)
    nowt = now_utc()
    todo = []
    for addr, p in pools.items():
        c = iso_to_epoch(p.get("created"))
        if c is None: continue
        age_h = (nowt - c) / 3600.0
        if age_h < 6.0 or age_h > 30.0: continue
        f = os.path.join(bdir, addr[:12] + ".json")
        if not os.path.exists(f): todo.append(addr)
    log("(b) cohort bars to fetch: %d" % len(todo))
    for i, addr in enumerate(todo):
        j = gt_get("https://api.geckoterminal.com/api/v2/networks/solana/pools/%s/ohlcv/minute"
                   % addr, {"aggregate": 1, "limit": 1000, "currency": "usd"})
        time.sleep(PACE)
        f = os.path.join(bdir, addr[:12] + ".json")
        try: raw = j["data"]["attributes"]["ohlcv_list"]
        except Exception:
            json.dump([], open(f, "w"))
            if (i + 1) % 20 == 0: log("  %d/%d" % (i + 1, len(todo)))
            continue
        bars = sorted([[int(b[0]), b[1], b[2], b[3], b[4], float(b[5])] for b in raw],
                      key=lambda b: b[0])
        json.dump(bars, open(f, "w"))
        if (i + 1) % 20 == 0: log("  %d/%d" % (i + 1, len(todo)))
    log("(b) done")

# ---------- (c) recall set ----------
def build_recall_set(max_pools=32):
    out_f = os.path.join(RIP, "_recall_set.json")
    existing = json.load(open(out_f)) if os.path.exists(out_f) else {}
    try:
        req = urllib.request.Request(DASH + "/api/universe-recorder?limit=5000",
                                     headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        ev = json.loads(raw)
    except Exception as e:
        log("(c) recorder pull FAILED %r" % e); return existing
    log("(c) recorder events: %d" % len(ev))
    # unique pairs that met the floor at some event WHILE YOUNG (age<=24h)
    best = {}
    for e in ev:
        pair = e.get("pair_address")
        mc, lq, ah = e.get("mcap"), e.get("liq_usd"), e.get("age_hours")
        ts = e.get("event_ts")
        if not pair or not isinstance(mc, (int, float)) or not isinstance(lq, (int, float)):
            continue
        if not isinstance(ah, (int, float)) or ah > 24: continue
        if mc >= 100_000 and lq >= 25_000:
            cur = best.get(pair)
            if cur is None or ts < cur["event_ts"]:
                best[pair] = {"event_ts": ts, "mcap": mc, "liq": lq,
                              "sym": e.get("symbol"), "token": e.get("token_address")}
    log("(c) young floor-meeting unique pairs: %d" % len(best))
    # created ts via pools/multi (30 per call, exact pool_created_at)
    pairs = sorted([p for p in best if p not in existing],
                   key=lambda p: best[p]["event_ts"])
    # spread sample across the whole event range for time diversity
    if len(pairs) > 66:
        step = len(pairs) / 66.0
        pairs = [pairs[int(i * step)] for i in range(66)]
    created = {}
    for i in range(0, min(len(pairs), 90), 30):
        batch = pairs[i:i + 30]
        j = gt_get("https://api.geckoterminal.com/api/v2/networks/solana/pools/multi/"
                   + ",".join(batch))
        time.sleep(PACE)
        if not j or "data" not in j: continue
        for d in j["data"]:
            a = d.get("attributes", {})
            c = iso_to_epoch(a.get("pool_created_at"))
            if c: created[a.get("address")] = c
    log("(c) created ts resolved: %d" % len(created))
    # keep pools YOUNG at event (<=24h) -> young-band traction positives
    cand = []
    for pair, meta in best.items():
        if pair in existing: continue
        c = created.get(pair)
        if c is None: continue
        et = meta["event_ts"]
        if isinstance(et, str): et = iso_to_epoch(et)
        if et is None: continue
        age_h = (et - c) / 3600.0
        if 0 <= age_h <= 24:
            cand.append((pair, c, et, age_h, meta))
    cand.sort(key=lambda x: x[1])  # by birth time (for split halves)
    log("(c) young-at-event traction pools: %d (fetching birth bars for %d)"
        % (len(cand), min(len(cand), max_pools)))
    bdir = os.path.join(RIP, "_gt_bars_b"); os.makedirs(bdir, exist_ok=True)
    for pair, c, et, age_h, meta in cand[:max_pools]:
        j = gt_get("https://api.geckoterminal.com/api/v2/networks/solana/pools/%s/ohlcv/minute"
                   % pair, {"aggregate": 1, "limit": 120, "currency": "usd",
                            "before_timestamp": int(c + 3600)})
        time.sleep(PACE)
        bars = []
        try:
            raw = j["data"]["attributes"]["ohlcv_list"]
            bars = sorted([[int(b[0]), b[1], b[2], b[3], b[4], float(b[5])] for b in raw],
                          key=lambda b: b[0])
        except Exception: pass
        json.dump(bars, open(os.path.join(bdir, pair[:12] + ".json"), "w"))
        existing[pair] = {"created_ep": c, "event_ts": et, "age_h_at_event": age_h,
                          "mcap_at_event": meta["mcap"], "liq_at_event": meta["liq"],
                          "sym": meta["sym"], "token": meta["token"],
                          "n_bars": len(bars)}
        json.dump(existing, open(out_f, "w"))
    log("(c) recall set size now: %d" % len(existing))
    return existing

if __name__ == "__main__":
    if "--recall-only" in sys.argv:
        build_recall_set(max_pools=40)
        log("ALL DONE")
    else:
        pools = refresh_newpools()
        fetch_cohort_bars(pools)
        build_recall_set(max_pools=40)
        log("ALL DONE")
