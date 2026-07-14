"""For each winner trip, reconstruct chart_mtf_score AT ENTRY by fetching GT 1m OHLC
(before_timestamp=buy_ts), resampling to 5m/15m/1h, and running the REAL alignment().
Also compute pc_h1/pc_h6/pc_h24 + off-high context. Classify vs the actual gate
(mtf<=-2 BLOCK, carve-out pc_h1>-20 rescue). Output -> scratch_mtf_results.json"""
import json, os, sys, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from feeds.candle_utils import Candle
from feeds.multi_timeframe import alignment

GT = "https://api.geckoterminal.com/api/v2"
UA = {"User-Agent": "Mozilla/5.0 (research)"}
PACE = 2.6
_last = [0.0]
_poolcache = {}

def _get(url):
    dt = time.time() - _last[0]
    if dt < PACE:
        time.sleep(PACE - dt)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                _last[0] = time.time()
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            _last[0] = time.time()
            if e.code == 429:
                time.sleep(8 * (attempt + 1)); continue
            return None
        except Exception:
            _last[0] = time.time()
            time.sleep(2); continue
    return None

def pool_for(mint):
    if mint in _poolcache:
        return _poolcache[mint]
    d = _get(f"{GT}/networks/solana/tokens/{mint}/pools?page=1")
    pool = None
    try:
        arr = (d or {}).get("data") or []
        # GT returns pools sorted; pick highest reserve_in_usd
        best = None; bestliq = -1
        for p in arr:
            liq = float((p.get("attributes") or {}).get("reserve_in_usd") or 0)
            if liq > bestliq:
                bestliq = liq; best = p
        if best:
            pool = best["attributes"]["address"]
    except Exception:
        pool = None
    _poolcache[mint] = pool
    return pool

def fetch_1m(pool, before_ts, limit=1000):
    url = (f"{GT}/networks/solana/pools/{pool}/ohlcv/minute"
           f"?aggregate=1&before_timestamp={before_ts}&limit={limit}&currency=usd")
    d = _get(url)
    try:
        lst = (((d or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    except Exception:
        return []
    # ohlcv_list: [ts, o, h, l, c, v], NEWEST-first per GT -> sort oldest-first
    rows = sorted(lst, key=lambda x: x[0])
    out = []
    for ts,o,h,l,c,v in rows:
        out.append(Candle(int(ts), float(o), float(h), float(l), float(c), float(v), int(ts)+60))
    return out

def resample(c1m, period_s):
    buckets = {}
    for c in c1m:
        k = c.open_time - (c.open_time % period_s)
        b = buckets.get(k)
        if b is None:
            buckets[k] = [c.open_time, c.open, c.high, c.low, c.close, c.volume]
        else:
            b[2] = max(b[2], c.high); b[3] = min(b[3], c.low)
            b[4] = c.close; b[5] += c.volume
    out = []
    for k in sorted(buckets):
        o = buckets[k]
        out.append(Candle(o[0], o[1], o[2], o[3], o[4], o[5], o[0]+period_s))
    return out

def pct_change_back(c1m, minutes):
    if not c1m:
        return None
    last = c1m[-1]
    target = last.open_time - minutes*60
    ref = None
    for c in c1m:
        if c.open_time <= target:
            ref = c
        else:
            break
    if ref is None or ref.close <= 0:
        return None
    return (last.close/ref.close - 1)*100

trips = json.load(open("scratch_trips.json"))
results = {}
total = sum(len([t for t in w["trips"] if t["closed"]]) for w in trips.values())
done = 0
for name, w in trips.items():
    rows = []
    for t in w["trips"]:
        if not t["closed"]:
            continue
        done += 1
        mint, bts, ret = t["mint"], t["buy_ts"], t["ret"]
        pool = pool_for(mint)
        rec = {"mint": mint[:12], "buy_ts": bts, "ret": ret, "pool": bool(pool)}
        if pool:
            c1 = fetch_1m(pool, bts)
            if len(c1) >= 5:
                c5 = resample(c1, 300); c15 = resample(c1, 900); c60 = resample(c1, 3600)
                al = alignment(c1, c5, c15, c60)
                rec["mtf"] = al["score"]
                rec["verdicts"] = al["verdicts"]
                rec["align"] = al["alignment"]
                rec["pc_h1"] = round(pct_change_back(c1, 60), 1) if pct_change_back(c1,60) is not None else None
                rec["pc_h6"] = round(pct_change_back(c1, 360), 1) if pct_change_back(c1,360) is not None else None
                rec["pc_h24"] = round(pct_change_back(c1, 1440), 1) if pct_change_back(c1,1440) is not None else None
                hi = max(c.high for c in c1); cur = c1[-1].close
                rec["off_high"] = round((cur/hi - 1)*100, 1) if hi > 0 else None
                rec["n1m"] = len(c1)
            else:
                rec["mtf"] = None; rec["n1m"] = len(c1)
        else:
            rec["mtf"] = None
        rows.append(rec)
        if done % 10 == 0:
            print(f"  ...{done}/{total}", flush=True)
    results[name] = {"addr": w["addr"], "rows": rows}
    json.dump(results, open("scratch_mtf_results.json","w"), indent=1)
    print(f"[{name}] done ({len(rows)} closed trips)", flush=True)

print("WROTE scratch_mtf_results.json")
