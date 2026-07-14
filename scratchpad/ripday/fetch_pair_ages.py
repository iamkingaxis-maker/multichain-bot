# Fetch pairCreatedAt (dexscreener) for in-window tape pairs missing age -> _pair_created_cache2.json
import json, os, glob, time
from curl_cffi import requests as cr

RIP = os.path.dirname(os.path.abspath(__file__))
S = cr.Session(impersonate="chrome")
HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}

pairs = set()
for f in glob.glob(os.path.join(RIP, "live_tapes", "tape_*.jsonl")) + glob.glob(os.path.join(RIP, "tape_*.jsonl")):
    with open(f, encoding="utf-8") as fh:
        line = fh.readline()
        try:
            t = json.loads(line)
            pairs.add(t["pair"])
        except Exception:
            pass

have = {}
try:
    tm = json.load(open(os.path.join(RIP, "token_meta.json")))
    from datetime import datetime
    for p, v in tm.items():
        if v.get("pool_created_at"):
            have[p] = 1
except Exception:
    pass
try:
    pc = json.load(open(os.path.join(RIP, "_pair_created_cache.json")))
    for p, v in pc.items():
        if v:
            have[p] = 1
except Exception:
    pass
out_path = os.path.join(RIP, "_pair_created_cache2.json")
out = {}
if os.path.exists(out_path):
    out = json.load(open(out_path))
for p in out:
    have[p] = 1

miss = [p for p in pairs if p not in have]
print("pairs:", len(pairs), "missing age:", len(miss))
for i, p in enumerate(miss):
    try:
        r = S.get("https://api.dexscreener.com/latest/dex/pairs/solana/%s" % p, timeout=20, headers=HDR)
        j = r.json() if r.status_code == 200 else {}
        pr = (j.get("pairs") or [None])[0] or j.get("pair") or {}
        ca = pr.get("pairCreatedAt")
        out[p] = ca / 1000.0 if ca else None
    except Exception:
        out[p] = None
    if i % 10 == 0:
        json.dump(out, open(out_path, "w"))
        print(i, p[:8], out[p])
    time.sleep(1.1)
json.dump(out, open(out_path, "w"))
print("DONE", len(out))
