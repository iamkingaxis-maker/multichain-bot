"""Checkpointing Solana RPC helper for the rug-forensics mine.
Uses core.rpc_pool (Alchemy key + public fallbacks). NEVER Helius.
Every heavy result is the caller's responsibility to persist immediately.
Pacing ~ set by caller; this lib rotates endpoints + backs off on 429/errors.
"""
from __future__ import annotations
import json, os, sys, time, urllib.request, urllib.error

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from core.rpc_pool import rpc_pool  # noqa: E402

POOL = rpc_pool()
_ORDER = list(range(len(POOL)))

SYSTEM_PROGRAM = "11111111111111111111111111111111"
PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_SWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
TOKEN_PROG = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
KNOWN_PROGRAMS = {
    TOKEN_PROG, "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL", SYSTEM_PROGRAM,
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP", "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
    PUMP_FUN, PUMP_SWAP,
    "ComputeBudget111111111111111111111111111111",
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
}

_calls = {"n": 0, "err": 0}


def rpc(method, params, retries=5, pace=0.35):
    """Single JSON-RPC call with endpoint rotation + exp backoff. Returns result or None."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last = None
    for attempt in range(retries):
        url = POOL[_ORDER[(_calls["n"] + attempt) % len(POOL)]]
        try:
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json", "User-Agent": "Mozilla/5.0 rugforensics"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())
            _calls["n"] += 1
            if "error" in data:
                last = data["error"]
                # rate-limit / server busy -> backoff
                time.sleep(min(8, 0.6 * (2 ** attempt)))
                continue
            time.sleep(pace)
            return data.get("result")
        except urllib.error.HTTPError as e:
            last = f"HTTP{e.code}"
            time.sleep(min(10, 0.8 * (2 ** attempt)))
        except Exception as e:
            last = str(e)[:80]
            time.sleep(min(8, 0.6 * (2 ** attempt)))
    _calls["err"] += 1
    return None


def sigs_for(addr, limit=1000, before=None):
    p = [addr, {"limit": limit}]
    if before:
        p[1]["before"] = before
    return rpc("getSignaturesForAddress", p) or []


def oldest_sigs(addr, max_pages=6, page=1000):
    """Page back to the earliest signatures. Returns (all_sigs_newest_first, hit_creation_bool)."""
    allsigs, before, hit = [], None, False
    for _ in range(max_pages):
        batch = sigs_for(addr, limit=page, before=before)
        if not batch:
            hit = True
            break
        allsigs.extend(batch)
        if len(batch) < page:
            hit = True
            break
        before = batch[-1]["signature"]
    return allsigs, hit


# archive-capable endpoints (serve historical getTransaction; publics prune)
ARCHIVE = [u for u in POOL if ("alchemy" in u or "mainnet-beta" in u)]


def get_tx(sig):
    """Historical tx: only hit archive endpoints, retry (publics return null result)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
                       "params": [sig, {"encoding": "jsonParsed",
                                        "maxSupportedTransactionVersion": 0}]}).encode()
    for attempt in range(6):
        url = ARCHIVE[attempt % len(ARCHIVE)] if ARCHIVE else POOL[0]
        try:
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json", "User-Agent": "Mozilla/5.0 rugforensics"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())
            _calls["n"] += 1
            if data.get("result") is not None:
                time.sleep(0.35)
                return data["result"]
            time.sleep(min(6, 0.5 * (2 ** attempt)))
        except Exception:
            time.sleep(min(6, 0.5 * (2 ** attempt)))
    return None


def largest_accounts(mint):
    r = rpc("getTokenLargestAccounts", [mint])
    return (r or {}).get("value") or []


def account_owner(token_acct):
    r = rpc("getAccountInfo", [token_acct, {"encoding": "jsonParsed"}])
    if not r:
        return None
    info = (((r.get("value") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
    return info.get("owner")


def token_supply(mint):
    r = rpc("getTokenSupply", [mint])
    if not r:
        return None
    v = r.get("value") or {}
    try:
        return float(v.get("amount")) / (10 ** int(v.get("decimals")))
    except Exception:
        return None


def stats():
    return dict(_calls)
