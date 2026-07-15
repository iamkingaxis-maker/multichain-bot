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
# rh_state_dir() mirror (core.rh_live_execution): RH_LIVE_STATE_DIR or the
# default tape dir. Kept as plain path logic so this uploader stays stdlib-only
# (no heavy web3 import — the whole point of the ~70MB lane service).
STATE_DIR = os.environ.get("RH_LIVE_STATE_DIR") or os.path.join(
    "scratchpad", "robinhood_tapes")
LEDGER = os.path.join("scratchpad", "robinhood_tapes", "rh_paper_trades.jsonl")
WALLET_TRUTH = os.path.join(STATE_DIR, "rh_wallet_truth.json")


def _auth_header() -> str:
    return "Basic " + base64.b64encode(AUTH.encode()).decode()


def _push_wallet_truth():
    """Ship the lane's keyless on-chain wallet-truth JSON to the dashboard so
    the RH WALLET card renders the live balance (mirror of /api/wallet-truth for
    Solana). ENV-DRIVEN NO-OP: unless RH_WALLET_ADDRESS is set AND the lane has
    written the snapshot, this does nothing. Idempotent — POSTs the current
    snapshot each cycle; the dashboard just overwrites the single stored copy.
    FAIL-OPEN: any error is printed and swallowed (never blocks the ledger push)."""
    if not os.environ.get("RH_WALLET_ADDRESS"):
        return
    if not os.path.exists(WALLET_TRUTH):
        print("[rh-upload] no wallet-truth snapshot yet")
        return
    try:
        with open(WALLET_TRUTH, encoding="utf-8") as f:
            wt = json.load(f)
    except Exception as e:
        print("[rh-upload] wallet-truth read failed:", e)
        return
    req = urllib.request.Request(
        BASE + "/api/rh-wallet-truth/ingest",
        data=json.dumps(wt).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": _auth_header()},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("[rh-upload] wallet-truth", r.read().decode()[:200])
    except Exception as e:
        print("[rh-upload] wallet-truth push failed:", e)


def _push_ledger():
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
    # CHUNKED (2026-07-15 fix — the SECOND ledger-blackout cause). This POSTed
    # the WHOLE ledger in ONE body. aiohttp's default client_max_size is 1MB and
    # the dashboard sets none; once the ledger outgrew that, request.json() threw
    # and the handler's except returned **HTTP 400** — so EVERY upload failed
    # forever and the book silently froze mid-session (07-15: died at 14:48 with
    # the lane still trading). An unbounded-and-growing payload is a guaranteed
    # future outage, so send bounded batches instead of raising the cap. Safe to
    # chunk: the endpoint appends + de-dups on (ts,ev,pool), so batches and
    # re-sends are idempotent. A failed batch is logged and does NOT abort the
    # rest — partial progress beats another all-or-nothing blackout.
    #
    # APPEND/MERGE (2026-07-13): the RH lane runs on an EPHEMERAL Railway
    # container — its local ledger resets on every redeploy. The old replace=1
    # (full-sync) OVERWROTE accumulated history with just the current session, so
    # racers could never reach n>=30. Append-mode dedups on (ts,ev,pool) —
    # re-sending a session is idempotent AND cross-session rows accumulate.
    CHUNK = 300
    sent = failed = 0
    for i in range(0, len(rows), CHUNK):
        batch = rows[i:i + CHUNK]
        req = urllib.request.Request(
            BASE + "/api/rh-paper/ingest",
            data=json.dumps(batch).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": _auth_header()},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
            sent += len(batch)
        except Exception as e:
            failed += len(batch)
            print(f"[rh-upload] batch {i//CHUNK} ({len(batch)} rows) failed: "
                  f"{type(e).__name__}: {str(e)[:100]}")
    print(f"[rh-upload] ledger: {sent}/{len(rows)} rows pushed"
          + (f", {failed} FAILED" if failed else ""))


def main():
    _push_ledger()
    _push_wallet_truth()


if __name__ == "__main__":
    main()
