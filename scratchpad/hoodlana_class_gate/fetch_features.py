"""Fetch rugcheck /report for winner + universe mints, compute holder features,
append each row to features.jsonl IMMEDIATELY. Pace ~2.5s, single process, backoff on 429.
Resumable: skips mints already in features.jsonl."""
import json, os, sys, time, urllib.request, urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(BASE, "..", "..")))
from core.holder_features import compute_holder_features  # noqa: E402

FEAT = os.path.join(BASE, "features.jsonl")
RAW = os.path.join(BASE, "raw")
os.makedirs(RAW, exist_ok=True)

coh = json.load(open(os.path.join(BASE, "cohorts.json")))
winners_any = set(json.load(open(os.path.join(BASE, "winners_anybot.json"))))
ds = json.load(open(os.path.join(BASE, "..", "rug_forensics", "death_split.json")))
alive = set(ds["alive"])

targets = []  # (mint, tags)
tagmap = {}
for m in winners_any:
    tagmap.setdefault(m, set()).add("winner_anybot")
for m in coh["winners_all"]:
    tagmap.setdefault(m, set()).add("winner_strict")
for u in coh["universe_recent50"]:
    tagmap.setdefault(u["mint"], set()).add("universe50")

# only fetch: alive winners (current-state approximates entry-state) + all universe50
fetch = set()
for m, tags in tagmap.items():
    if "universe50" in tags:
        fetch.add(m)
    elif m in alive:
        fetch.add(m)

done = set()
if os.path.exists(FEAT):
    for ln in open(FEAT):
        try:
            done.add(json.loads(ln)["mint"])
        except Exception:
            pass

todo = sorted(fetch - done)
print(f"targets total={len(fetch)} done={len(done)} todo={len(todo)}", flush=True)

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

for i, m in enumerate(todo):
    rc = fetch_report(m)
    row = {"mint": m, "tags": sorted(tagmap[m]), "alive": m in alive}
    if rc is None:
        row["fetch_err"] = "exhausted"
    elif "_err" in rc:
        row["fetch_err"] = rc["_err"]
    else:
        feats = compute_holder_features(rc)
        row.update(feats)
        row["graphInsidersDetected"] = rc.get("graphInsidersDetected")
        row["rc_lpLockedPct_toplevel"] = rc.get("lpLockedPct")
        # trimmed raw for re-analysis without refetch
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
                                      for kk in ("baseUSD", "quoteUSD", "lpLockedPct",
                                                 "quotePrice", "basePrice", "tokenSupply")}}
                              for mm in (rc.get("markets") or [])]
        with open(os.path.join(RAW, m + ".json"), "w") as f:
            json.dump(trim, f)
    with open(FEAT, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(f"[{i+1}/{len(todo)}] {m[:8]} tags={row['tags']} sh={row.get('shoulder_11_20_pct')} "
          f"pool={row.get('pool_topholder_pct')} t10={row.get('top10_holder_pct')} "
          f"hold={row.get('total_holders')} err={row.get('fetch_err')}", flush=True)
    time.sleep(2.5)
print("DONE", flush=True)
