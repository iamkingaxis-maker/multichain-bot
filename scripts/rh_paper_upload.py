# scripts/rh_paper_upload.py
"""Push the local RH paper-lane ledger to the dashboard's ingest endpoint
(idempotent — server de-dups on (ts, ev, pool)). Run per-session after/while
the lane runs; the dashboard's Robinhood Chain card renders what lands.

Usage: python scripts/rh_paper_upload.py
"""
import base64
import json
import os
import urllib.request

BASE = os.environ.get(
    "DASH_BASE", "https://gracious-inspiration-production.up.railway.app")
AUTH = os.environ.get("DASH_AUTH", "jcole:pMIwPSmRmoPfteWViuGgjaTdnx5JfO-g-e6-_zjdlmo")
LEDGER = os.path.join("scratchpad", "robinhood_tapes", "rh_paper_trades.jsonl")


def main():
    if not os.path.exists(LEDGER):
        print("[rh-upload] no local ledger yet")
        return
    rows = []
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    if not rows:
        print("[rh-upload] ledger empty")
        return
    req = urllib.request.Request(
        # full-sync: the LOCAL ledger is the source of truth — corrections
        # (e.g. audited row fixes) propagate instead of being dedupe-skipped
        BASE + "/api/rh-paper/ingest?replace=1",
        data=json.dumps(rows).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Basic " + base64.b64encode(AUTH.encode()).decode()},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        print("[rh-upload]", r.read().decode()[:200])


if __name__ == "__main__":
    main()
