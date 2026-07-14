# Fetch/extend minute OHLC for BNL losing-round pairs -> _gt_bars/{pair12}.json (merge)
import json, os, time
from curl_cffi import requests as cr

RIP = os.path.dirname(os.path.abspath(__file__))
GTS = cr.Session(impersonate="chrome")
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}

def gt(url, tries=4):
    for t in range(tries):
        try:
            r = GTS.get(url, timeout=25, headers=HDR)
            if r.status_code == 200:
                return r.json()
            time.sleep(9 if r.status_code == 429 else 3)
        except Exception:
            time.sleep(4)
    return None

need = json.load(open(os.path.join(RIP, "_bnl_refetch_pairs.json")))
print("pairs to (re)fetch:", len(need))
os.makedirs(os.path.join(RIP, "_gt_bars"), exist_ok=True)
for i, (p, (start_ep, end_ep)) in enumerate(sorted(need.items())):
    start_ep = int(start_ep) - 1800
    end_ep = int(end_ep) + 3600
    path = os.path.join(RIP, "_gt_bars", "%s.json" % p[:12])
    allbars = {}
    if os.path.exists(path):
        try:
            for b in json.load(open(path)):
                allbars[int(b[0])] = b
        except Exception:
            pass
    # skip if existing already covers window
    if allbars:
        ks = sorted(allbars)
        if ks[0] <= start_ep + 1800 and ks[-1] >= end_ep - 3600:
            print(i, p[:12], "already covered"); continue
    before = end_ep
    for page in range(6):
        url = ("https://api.geckoterminal.com/api/v2/networks/solana/pools/%s/"
               "ohlcv/minute?aggregate=1&limit=1000&currency=usd&before_timestamp=%d" % (p, before))
        j = gt(url)
        time.sleep(3.0)
        bars = (((j or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        if not bars:
            break
        for b in bars:
            allbars[int(b[0])] = b
        mn = min(int(b[0]) for b in bars)
        if mn <= start_ep or len(bars) < 900:
            break
        before = mn
    out = sorted(allbars.values(), key=lambda b: b[0])
    json.dump(out, open(path, "w"))
    print(i, p[:12], "bars:", len(out), flush=True)
print("DONE")
