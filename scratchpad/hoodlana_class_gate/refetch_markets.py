"""Corrective pass: first-pass trim dropped markets[].pubkey (needed to identify pool
vaults in topHolders since rugcheck no longer tags them). Refresh raw/*.json with the
upgraded trim. Skips mints whose raw already has markets_lp[].pubkey."""
import json, os, glob, time, urllib.request, urllib.error
BASE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(BASE, "raw")

def fetch_report(mint):
    url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report"
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(min(30, 5 * (2 ** attempt)))
                continue
            if e.code in (400, 404):
                return {"_err": f"HTTP{e.code}"}
            time.sleep(min(15, 2 * (2 ** attempt)))
        except Exception:
            time.sleep(min(15, 2 * (2 ** attempt)))
    return None
todo = []
for f in glob.glob(os.path.join(RAW, "*.json")):
    d = json.load(open(f))
    ml = d.get("markets_lp") or []
    if not ml or any(m.get("pubkey") for m in ml):
        continue
    todo.append(os.path.basename(f)[:-5])
print(f"refetch todo: {len(todo)}", flush=True)
for i, m in enumerate(sorted(todo)):
    rc = fetch_report(m)
    if not rc or "_err" in rc:
        print(f"[{i+1}] {m[:8]} ERR {rc and rc.get('_err')}", flush=True)
        time.sleep(2.5)
        continue
    trim = {k: rc.get(k) for k in ("topHolders", "totalHolders", "graphInsidersDetected",
                                    "creator", "score", "score_normalised", "lpLockedPct")}
    trim["markets_lp"] = [{"mintLP": (mm or {}).get("mintLP"),
                           "pubkey": (mm or {}).get("pubkey"),
                           "marketType": (mm or {}).get("marketType"),
                           "liquidityA": (mm or {}).get("liquidityA"),
                           "liquidityB": (mm or {}).get("liquidityB"),
                           "liquidityAAccount": (mm or {}).get("liquidityAAccount"),
                           "liquidityBAccount": (mm or {}).get("liquidityBAccount"),
                           "lp": {kk: ((mm or {}).get("lp") or {}).get(kk)
                                  for kk in ("baseUSD", "quoteUSD", "lpLockedPct")}}
                          for mm in (rc.get("markets") or [])]
    with open(os.path.join(RAW, m + ".json"), "w") as f:
        json.dump(trim, f)
    print(f"[{i+1}/{len(todo)}] {m[:8]} markets={len(trim['markets_lp'])}", flush=True)
    time.sleep(2.5)
print("DONE", flush=True)
