# Extend minute-bar coverage for ALL pairs with trades >= 2026-07-03, to last trade + 4h.
# Merges into _gt_bars/{pair12}.json. Modeled on fetch_extend_bars.py.
import json, glob, os, time
from datetime import datetime
from curl_cffi import requests as cr

RIP = os.path.dirname(os.path.abspath(__file__))
GTS = cr.Session(impersonate="chrome")
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}


def gt(url, tries=5):
    for t in range(tries):
        try:
            r = GTS.get(url, timeout=25, headers=HDR)
            if r.status_code == 200:
                return r.json()
            time.sleep(10 if r.status_code == 429 else 3)
        except Exception:
            time.sleep(4)
    return None


# pairs with trades on/after 07-03, with vol-weight to prioritize
need = {}
for f in glob.glob(os.path.join(RIP, "live_tapes", "tape_*.jsonl")) + glob.glob(os.path.join(RIP, "tape_*.jsonl")):
    for line in open(f, encoding="utf-8"):
        try:
            t = json.loads(line)
        except Exception:
            continue
        if t["ts"] < "2026-07-03":
            continue
        ep = datetime.fromisoformat(t["ts"]).timestamp()
        v = need.setdefault(t["pair"], [ep, ep, 0.0])
        v[0] = min(v[0], ep)
        v[1] = max(v[1], ep)
        v[2] += t["volume_usd"]

# existing coverage
cov = {}
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if d.get("bars"):
        p, bl = d["pair"], d["bars"]
        c = cov.setdefault(p, [bl[0][0], bl[-1][0]])
        c[0] = min(c[0], bl[0][0])
        c[1] = max(c[1], bl[-1][0])
p12 = {p[:12]: p for p in need}
for dd in ("_gt_bars", "_gt_bars_b"):
    for f in glob.glob(os.path.join(RIP, dd, "*.json")):
        stem = os.path.basename(f).split(".")[0]
        p = p12.get(stem)
        if not p:
            continue
        try:
            b = json.load(open(f))
        except Exception:
            continue
        bl = b if isinstance(b, list) else b.get("bars", [])
        if not bl:
            continue
        ts = sorted(x[0] for x in bl)
        c = cov.setdefault(p, [ts[0], ts[-1]])
        c[0] = min(c[0], ts[0])
        c[1] = max(c[1], ts[-1])

now = time.time()
jobs = []
for p, (mn, mx, vol) in need.items():
    c = cov.get(p)
    want_end = min(mx + 4 * 3600, now)
    have_end = c[1] if c else None
    if have_end is None or want_end - have_end > 900:
        jobs.append((p, vol, mn, want_end, have_end))
jobs.sort(key=lambda x: -x[1])
print("jobs:", len(jobs), "of", len(need), "pairs", flush=True)

os.makedirs(os.path.join(RIP, "_gt_bars"), exist_ok=True)
for p, vol, mn, want_end, have_end in jobs:
    floor_ep = (have_end - 600) if have_end else (mn - 6 * 3600)
    allbars = {}
    path = os.path.join(RIP, "_gt_bars", "%s.json" % p[:12])
    if os.path.exists(path):
        try:
            old = json.load(open(path))
            bl = old if isinstance(old, list) else old.get("bars", [])
            for b in bl:
                allbars[int(b[0])] = b
        except Exception:
            pass
    before = int(want_end)
    got = 0
    for page in range(4):
        url = ("https://api.geckoterminal.com/api/v2/networks/solana/pools/%s/"
               "ohlcv/minute?aggregate=1&limit=1000&currency=usd&before_timestamp=%d" % (p, before))
        j = gt(url)
        time.sleep(3.0)
        bars = (((j or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        if not bars:
            break
        for b in bars:
            allbars[int(b[0])] = b
        got += len(bars)
        mnb = min(int(b[0]) for b in bars)
        if mnb <= floor_ep or len(bars) < 900:
            break
        before = mnb
    try:
        json.dump(sorted(allbars.values(), key=lambda b: b[0]), open(path, "w"))
    except OSError as ex:
        print(p[:12], "WRITE-FAIL", ex, flush=True)
        try:
            time.sleep(2)
            json.dump(sorted(allbars.values(), key=lambda b: b[0]), open(path, "w"))
        except OSError as ex2:
            print(p[:12], "WRITE-FAIL-2 (skipped)", ex2, flush=True)
    print(p[:12], "vol=%.0f fetched=%d total=%d" % (vol, got, len(allbars)), flush=True)
print("DONE")
