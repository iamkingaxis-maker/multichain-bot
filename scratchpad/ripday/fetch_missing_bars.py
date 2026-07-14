# Fetch minute OHLC for tape pairs lacking bars -> _gt_bars/{pair12}.json (bar list)
import json, glob, os, time, sys
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

# figure out missing pairs (same logic as build_ledger2)
seen_pairs = {}
for f in glob.glob(os.path.join(RIP, "live_tapes", "tape_*.jsonl")) + glob.glob(os.path.join(RIP, "tape_*.jsonl")):
    for line in open(f, encoding="utf-8"):
        try:
            t = json.loads(line)
        except Exception:
            continue
        if t["ts"] < "2026-07-01":
            continue
        v = seen_pairs.setdefault(t["pair"], [0, None, None, t.get("token", "")])
        v[0] += 1
        if v[1] is None or t["ts"] < v[1]:
            v[1] = t["ts"]
        if v[2] is None or t["ts"] > v[2]:
            v[2] = t["ts"]
        if not v[3] and t.get("token"):
            v[3] = t["token"]

have = set()
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    d = json.load(open(f))
    if d.get("bars"):
        have.add(d["pair"])
p12 = {p[:12]: p for p in seen_pairs}
for f in glob.glob(os.path.join(RIP, "_gt_bars", "*.json")):
    stem = os.path.basename(f).split(".")[0]
    if stem in p12:
        have.add(p12[stem])

idx = {}
try:
    idx = json.load(open(os.path.join(RIP, "tape_index.json")))
except Exception:
    pass

miss = sorted([(p, v) for p, v in seen_pairs.items() if p not in have], key=lambda x: -x[1][0])
print("missing:", len(miss))
from datetime import datetime, timezone
os.makedirs(os.path.join(RIP, "_gt_bars"), exist_ok=True)
for p, v in miss:
    n, oldest, newest, tok = v
    if not tok and p in idx:
        tok = idx[p].get("token", "")
    end_ep = int(datetime.fromisoformat(newest).timestamp()) + 3600
    start_ep = int(datetime.fromisoformat(oldest).timestamp()) - 6 * 3600
    allbars = {}
    before = end_ep
    pool = p
    tried_fallback = False
    for page in range(4):
        url = ("https://api.geckoterminal.com/api/v2/networks/solana/pools/%s/"
               "ohlcv/minute?aggregate=1&limit=1000&currency=usd&before_timestamp=%d" % (pool, before))
        j = gt(url)
        time.sleep(3.0)
        bars = (((j or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        if not bars and not allbars and not tried_fallback and tok:
            tried_fallback = True
            j2 = gt("https://api.geckoterminal.com/api/v2/networks/solana/tokens/%s/pools" % tok)
            time.sleep(3.0)
            pools = ((j2 or {}).get("data") or [])
            if pools:
                pool = pools[0].get("attributes", {}).get("address") or pool
                continue
        if not bars:
            break
        for b in bars:
            allbars[int(b[0])] = b
        mn = min(int(b[0]) for b in bars)
        if mn <= start_ep or len(bars) < 900:
            break
        before = mn
    out = sorted(allbars.values(), key=lambda b: b[0])
    path = os.path.join(RIP, "_gt_bars", "%s.json" % p[:12])
    json.dump(out, open(path, "w"))
    print(p[:12], "bars:", len(out), "tok:", tok[:8] if tok else "?")
print("DONE")
