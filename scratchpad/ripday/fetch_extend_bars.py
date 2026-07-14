# Extend minute-bar coverage for winner-traded pairs up to last trade + 4h (cf horizon).
# Merges into _gt_bars/{pair12}.json. Core pairs first.
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


win = json.load(open(os.path.join(RIP, "winners_current.json")))
W = set(win["winners"])
core = {w for w, s in win["winners"].items() if s["realized"] > 0}

need = {}
seen = set()
for f in glob.glob(os.path.join(RIP, "live_tapes", "tape_*.jsonl")) + glob.glob(os.path.join(RIP, "tape_*.jsonl")):
    for line in open(f, encoding="utf-8"):
        try:
            t = json.loads(line)
        except Exception:
            continue
        if t["ts"] < "2026-07-01" or t["maker"] not in W:
            continue
        key = (t["pair"], t["ts"], t["maker"], t["kind"], round(t["volume_usd"], 4))
        if key in seen:
            continue
        seen.add(key)
        ep = datetime.fromisoformat(t["ts"]).timestamp()
        v = need.setdefault(t["pair"], [ep, ep, 0])
        v[0] = min(v[0], ep)
        v[1] = max(v[1], ep)
        if t["maker"] in core:
            v[2] += 1

cov = {}
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    d = json.load(open(f))
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
        b = json.load(open(f))
        bl = b if isinstance(b, list) else b.get("bars", [])
        if not bl:
            continue
        ts = sorted(x[0] for x in bl)
        c = cov.setdefault(p, [ts[0], ts[-1]])
        c[0] = min(c[0], ts[0])
        c[1] = max(c[1], ts[-1])

jobs = []
for p, (mn, mx, nc) in need.items():
    c = cov.get(p)
    want_end = mx + 4 * 3600
    have_end = c[1] if c else None
    if have_end is None or want_end - have_end > 900:
        jobs.append((p, nc, mn, want_end, have_end))
jobs.sort(key=lambda x: -x[1])
print("jobs:", len(jobs), "core-relevant:", sum(1 for j in jobs if j[1] > 0), flush=True)

os.makedirs(os.path.join(RIP, "_gt_bars"), exist_ok=True)
for p, nc, mn, want_end, have_end in jobs:
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
    json.dump(sorted(allbars.values(), key=lambda b: b[0]), open(path, "w"))
    print(p[:12], "core_trades=%d fetched=%d total=%d" % (nc, got, len(allbars)), flush=True)
print("DONE")
