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
        # APPEND/MERGE (2026-07-13 fix): the RH lane runs on an EPHEMERAL
        # Railway container — its local ledger resets to empty on every
        # redeploy. The old replace=1 (full-sync) therefore OVERWROTE the
        # persistent accumulated history with just the current session on
        # each redeploy, so racers could never reach n>=30 (grading clock
        # reset every deploy). Append-mode dedups on (ts,ev,pool) — re-sending
        # a session is idempotent AND cross-session rows accumulate. (No row
        # corrections happen on the autonomous lane, so the replace=1
        # correction-propagation use case does not apply here.)
        BASE + "/api/rh-paper/ingest",
        data=json.dumps(rows).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Basic " + base64.b64encode(AUTH.encode()).decode()},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        print("[rh-upload]", r.read().decode()[:200])


if __name__ == "__main__":
    main()
