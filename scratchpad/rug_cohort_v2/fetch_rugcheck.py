"""Step 3b: rugcheck /report features for the v2 cohort.
Targets: ALL final-catastrophic mints + alive winners (anybot) + alive universe rest.
Reuses hoodlana_class_gate/raw/*.json (fetched 07-11, still fresh).
Appends every row to features.jsonl immediately; raw trimmed report to raw/.
Pace 2.5s, backoff on 429. Resumable.
"""
import json, os, sys, time, urllib.request, urllib.error

REPO = r"C:\Users\jcole\multichain-bot"
V2 = os.path.join(REPO, "scratchpad", "rug_cohort_v2")
OLD_RAW = os.path.join(REPO, "scratchpad", "hoodlana_class_gate", "raw")
FEAT = os.path.join(V2, "features.jsonl")
RAW = os.path.join(V2, "raw")
os.makedirs(RAW, exist_ok=True)

labels = {}
for line in open(os.path.join(V2, "labels_final.jsonl"), encoding="utf-8"):
    r = json.loads(line)
    labels[r["mint"]] = r
win = json.load(open(os.path.join(V2, "winners.json")))
anybot, strict = set(win["anybot"]), set(win["strict"])

cat = [m for m, r in labels.items() if r["label"] == "catastrophic"]
alive_win = [m for m, r in labels.items() if r["label"] == "alive" and m in anybot]
alive_rest = [m for m, r in labels.items() if r["label"] == "alive" and m not in anybot]
dead = [m for m, r in labels.items() if r["label"] == "dead"]
print(f"cat={len(cat)} alive_win={len(alive_win)} alive_rest={len(alive_rest)} dead={len(dead)}",
      flush=True)

# target order: catastrophic first (the scarce side), then alive winners, then alive rest
targets = cat + alive_win + alive_rest

done = set()
if os.path.exists(FEAT):
    for ln in open(FEAT, encoding="utf-8"):
        try:
            done.add(json.loads(ln)["mint"])
        except Exception:
            pass

def load_old_raw(m):
    p = os.path.join(OLD_RAW, m + ".json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return None

def fetch_report(mint):
    url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report"
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                       "Accept": "application/json"})
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

def trim(rc):
    t = {k: rc.get(k) for k in ("topHolders", "totalHolders", "graphInsidersDetected",
                                "creator", "score", "score_normalised", "lpLockedPct")}
    t["markets_lp"] = [{"mintLP": (mm or {}).get("mintLP"),
                        "pubkey": (mm or {}).get("pubkey"),
                        "marketType": (mm or {}).get("marketType"),
                        "liquidityA": (mm or {}).get("liquidityA"),
                        "liquidityB": (mm or {}).get("liquidityB"),
                        "liquidityAAccount": (mm or {}).get("liquidityAAccount"),
                        "liquidityBAccount": (mm or {}).get("liquidityBAccount")}
                       for mm in (rc.get("markets") or [])]
    return t

todo = [m for m in targets if m not in done]
print(f"targets={len(targets)} done={len(done)} todo={len(todo)}", flush=True)

for i, m in enumerate(todo):
    lab = labels[m]
    row = {"mint": m, "label": lab["label"], "ret_pct": lab.get("ret_pct"),
           "is_winner_any": m in anybot, "is_winner_strict": m in strict,
           "entry_ts": lab.get("entry_ts")}
    old = load_old_raw(m)
    if old is not None:
        t = old
        row["src"] = "old_raw"
    else:
        rc = fetch_report(m)
        if rc is None:
            row["fetch_err"] = "exhausted"
            t = None
        elif "_err" in rc:
            row["fetch_err"] = rc["_err"]
            t = None
        else:
            t = trim(rc)
            row["src"] = "fresh"
        time.sleep(2.5)
    if t is not None:
        json.dump(t, open(os.path.join(RAW, m + ".json"), "w"))
        row["total_holders"] = t.get("totalHolders")
        row["n_topholders"] = len(t.get("topHolders") or [])
        row["graph_insiders"] = t.get("graphInsidersDetected")
    with open(FEAT, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    if (i + 1) % 20 == 0 or row.get("fetch_err"):
        print(f"[{i+1}/{len(todo)}] {m[:8]} label={row['label']} src={row.get('src')} "
              f"hold={row.get('total_holders')} err={row.get('fetch_err')}", flush=True)
print("DONE", flush=True)
