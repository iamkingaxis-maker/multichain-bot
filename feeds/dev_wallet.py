"""
Dev / creator wallet tracking — remaining sell capacity.

Most rugs we eat happen because the deployer still holds X% of supply
and dumps it. Tracking dev wallet balance over time tells us how much
"loaded gun" remains.

Approach:
  1. First time we see a token, query Solana RPC for top largest
     accounts (`getTokenLargestAccounts`). The top non-LP holder is our
     proxy for "dev/creator/treasury wallet" — the entity with single
     largest sell capacity. We persist:
       - dev_wallet_addr
       - dev_baseline_pct_supply (% of total supply held)
       - first_seen_ts

  2. Each subsequent scan, re-query top accounts. Compute:
       - dev_pct_remaining       — current % of supply held by that addr
       - dev_pct_dumped          — baseline_pct - current_pct
       - dev_balance_change_pct  — relative to baseline
       - dev_baseline_age_hours

  3. When supply % changes by > tolerance, log it and update baseline
     timestamp (still tracking same address, but new "phase").

State persists in DATA_DIR/dev_wallet_baselines.json. Atomic JSON
write per-update. Pruning: drop entries unseen for >7 days.

Fail-open: ALL functions return {} on any error (RPC down, missing
data, address parse fail). Bot continues without these features.

LP-account heuristic: Solana AMMs (Raydium, Orca, PumpSwap) keep their
share of the token in a program-derived address, NOT a regular wallet.
We exclude any "owner" that's a known program ID. The remaining top
holder is typically the deployer or treasury.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

# Use the user-configured RPC if available (Railway env), else public.
# Public RPC heavily rate-limits getTokenLargestAccounts (429s); a paid
# RPC will yield much higher coverage. We never hardcode Helius (per
# user directive); the env var is user-controlled.
SOLANA_RPC_URL = os.environ.get(
    "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
)

# Single-flight: at most ONE getTokenLargestAccounts call in flight
# at a time. Public RPC bursts trigger 429 on this method.
_RPC_SEMAPHORE: Optional[asyncio.Semaphore] = None

# ── Cost-control knobs (2026-06-20) ─────────────────────────────────────
# The dev-wallet RPC chain (getTokenSupply + getTokenLargestAccounts +
# getAccountInfo x10) was the #1 Railway CPU/egress/memory(OOM) driver:
# dev_wallet_rpc=15.8s PER TOKEN on the main scan, re-paid every 300s.
# Creator-holdings is a SLOW-moving rug signal, so a much longer baseline
# TTL is fine and cuts re-fetches ~12x. All knobs env-tunable + reversible.
_DEFAULT_BASELINE_TTL_SECS = 3600.0   # was 300; 1h baseline is fine for the rug filter
_DEFAULT_RPC_TIMEOUT_S = 3.0          # per-call, was hardcoded 8
_DEFAULT_MAX_REFRESH_PER_CYCLE = 15   # cold/stale tokens refreshed per scan cycle
_DEFAULT_RPC_CONCURRENCY = 4          # parallel top-10 getAccountInfo gather


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return default


def _baseline_ttl_secs() -> float:
    return _env_float("DEV_WALLET_BASELINE_TTL_SECS", _DEFAULT_BASELINE_TTL_SECS)


def _rpc_timeout_s() -> float:
    return _env_float("DEV_WALLET_RPC_TIMEOUT_S", _DEFAULT_RPC_TIMEOUT_S)


def _max_refresh_per_cycle() -> int:
    return _env_int("DEV_WALLET_MAX_REFRESH_PER_CYCLE", _DEFAULT_MAX_REFRESH_PER_CYCLE)


def _rpc_concurrency() -> int:
    return max(1, _env_int("DEV_WALLET_RPC_CONCURRENCY", _DEFAULT_RPC_CONCURRENCY))

# Known program IDs we want to exclude as "dev wallets" (these are
# AMM/program accounts, not user wallets).
KNOWN_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # SPL Token program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",   # Associated token program
    "11111111111111111111111111111111",                # System program
    # AMM programs (their own token vaults end up here)
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",   # Raydium AMM v4
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",   # Raydium CLMM
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP",   # Orca legacy
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",    # Orca whirlpool
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",    # Pump.fun
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",    # PumpSwap AMM
}


def _baselines_path() -> str:
    data_dir = os.environ.get("DATA_DIR", "/data")
    if not os.path.isdir(data_dir):
        data_dir = "."
    return os.path.join(data_dir, "dev_wallet_baselines.json")


def _load_baselines() -> Dict[str, Dict[str, Any]]:
    path = _baselines_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[DevWallet] Failed to load baselines: {e}")
        return {}


def _save_baselines(data: Dict[str, Dict[str, Any]]) -> None:
    path = _baselines_path()
    out_dir = os.path.dirname(path) or "."
    try:
        os.makedirs(out_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f"[DevWallet] Failed to save baselines: {e}")


async def _rpc_call(session: aiohttp.ClientSession, method: str, params: list) -> Optional[Any]:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        async with session.post(
            SOLANA_RPC_URL, json=body,
            timeout=aiohttp.ClientTimeout(total=_rpc_timeout_s())
        ) as r:
            if r.status != 200:
                return None
            data = await r.json()
            return data.get("result")
    except Exception:
        return None


async def _get_token_supply(session: aiohttp.ClientSession, mint: str) -> Optional[float]:
    res = await _rpc_call(session, "getTokenSupply", [mint])
    if not res:
        return None
    val = (res.get("value") or {})
    raw_amount = val.get("amount")
    decimals = val.get("decimals")
    if raw_amount is None or decimals is None:
        return None
    try:
        return float(raw_amount) / (10 ** int(decimals))
    except Exception:
        return None


async def _get_largest_accounts(
    session: aiohttp.ClientSession, mint: str
) -> Optional[List[Dict[str, Any]]]:
    """Return list of {address, uiAmount, amount} for the 20 largest holders."""
    res = await _rpc_call(session, "getTokenLargestAccounts", [mint])
    if not res:
        return None
    return (res.get("value") or [])


async def _resolve_owner(
    session: aiohttp.ClientSession, token_account_addr: str
) -> Optional[str]:
    """Token accounts are owned by a wallet. We need the wallet, not
    the token account, to identify dev. getAccountInfo with jsonParsed
    encoding returns the owner."""
    res = await _rpc_call(session, "getAccountInfo", [
        token_account_addr,
        {"encoding": "jsonParsed"},
    ])
    if not res:
        return None
    val = (res.get("value") or {})
    parsed = ((val.get("data") or {}).get("parsed") or {})
    info = (parsed.get("info") or {})
    return info.get("owner")


async def _identify_dev_wallet(
    session: aiohttp.ClientSession, mint: str, total_supply: float
) -> Optional[Tuple[str, float]]:
    """
    Walk the top-largest accounts, resolve owners, return the first
    one that's NOT a known program ID. That's our dev/treasury proxy.
    Returns (owner_addr, pct_of_supply) or None.
    """
    accounts = await _get_largest_accounts(session, mint)
    if not accounts or total_supply <= 0:
        return None

    # Top-10 candidates (skip zero-balance/missing-addr up front).
    top = [
        (acc.get("address"), acc.get("uiAmount") or 0)
        for acc in accounts[:10]
        if acc.get("address") and (acc.get("uiAmount") or 0) > 0
    ]
    if not top:
        return None

    # PARALLELIZE owner resolution under a small bounded gather (was a serial
    # chain of up to 10 getAccountInfo calls @ 8s each — the bulk of the
    # 15.8s/token cost). Single shared session; concurrency env-bounded.
    sem = asyncio.Semaphore(_rpc_concurrency())

    async def _resolve_bounded(addr: str) -> Optional[str]:
        async with sem:
            return await _resolve_owner(session, addr)

    owners = await asyncio.gather(
        *(_resolve_bounded(addr) for addr, _ in top),
        return_exceptions=True,
    )
    # Preserve largest-first order: return the first non-program owner.
    for (addr, ui_amount), owner in zip(top, owners):
        if not owner or isinstance(owner, BaseException):
            continue
        if owner in KNOWN_PROGRAMS:
            continue
        pct = (ui_amount / total_supply) * 100.0
        return (owner, pct)
    return None


async def fetch_dev_features(
    mint: str, baselines: Dict[str, Dict[str, Any]],
    cache_only: bool = False,
    refresh_allowed: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Compute dev-wallet features for a token. Updates `baselines` in place
    when a new token is first seen. Returns dict suitable for entry_meta.

    Throttling: if a baseline exists and is younger than the env-tunable TTL
    (DEV_WALLET_BASELINE_TTL_SECS, default 1h), returns cached values with
    ZERO RPC. Creator-holdings is a SLOW-moving rug signal, so a 1h+ baseline
    is fine for filter_dev_dumping and cuts re-fetches ~12x vs the old 300s.

    cache_only=True (FAST-WATCH PATH, 2026-06-20): NEVER make an RPC call.
    On a warm baseline return the cached features; on a cache MISS return {}
    (fail-open — the feature is simply absent this fast tick, and the main
    scan / next tick refreshes it). This is the fix for the 16–35s fast-watch
    survivor stall: a cache-miss here used to fire a serial chain of up to
    ~12 Solana RPC calls (8s timeout each) under a global Semaphore(1),
    blocking the very fills the fast path exists to accelerate.

    refresh_allowed (per-cycle cap, 2026-06-20): optional zero-arg callable
    returning True if this cold/stale token may spend an RPC refresh THIS
    cycle. When it returns False (per-cycle budget exhausted), fail-open with
    {} — no RPC — and let the next cycle pick the token up. Fail-open: a
    None callable means "always allowed" (back-compat).
    """
    global _RPC_SEMAPHORE
    if _RPC_SEMAPHORE is None:
        _RPC_SEMAPHORE = asyncio.Semaphore(1)
    out: Dict[str, Any] = {}
    if not mint:
        return out
    key = mint  # don't lowercase Solana addrs (they're case-sensitive)
    now = time.time()
    ttl = _baseline_ttl_secs()

    # Fast path: baseline younger than TTL → use cached values, no RPC
    base = baselines.get(key)
    if base and (now - float(base.get("last_seen_ts") or 0)) < ttl:
        baseline_pct = float(base.get("baseline_pct_supply") or 0)
        cached_pct = float(base.get("last_pct_supply") or baseline_pct)
        return {
            "dev_wallet_addr": base.get("dev_wallet_addr"),
            "dev_pct_remaining": round(cached_pct, 4),
            "dev_baseline_pct": round(baseline_pct, 4),
            "dev_pct_dumped": round(max(0.0, baseline_pct - cached_pct), 4),
            "dev_balance_change_pct": round(
                ((cached_pct - baseline_pct) / baseline_pct * 100)
                if baseline_pct > 0 else 0.0,
                3,
            ),
            "dev_baseline_age_hours": round(
                (now - float(base.get("baseline_ts") or now)) / 3600.0, 2
            ),
            "dev_features_source": "cache",
        }

    # FAST-WATCH CACHE-ONLY: a cache miss must NOT hit the RPC chain (the 16–35s
    # survivor stall). Fail-open with no features; the main scan refreshes them.
    if cache_only:
        return out

    # PER-CYCLE REFRESH CAP: a cold-universe cycle can churn hundreds of new
    # tokens; each cold refresh is several Solana RPC round-trips. Bound how
    # many cold/stale tokens spend an RPC refresh per scan cycle — the rest
    # fail-open this cycle and get picked up next cycle. Fail-open if the
    # gate itself errors (NEVER block the scan on the budget check).
    if refresh_allowed is not None:
        try:
            if not refresh_allowed():
                return out
        except Exception:
            pass

    try:
        async with _RPC_SEMAPHORE:
            async with aiohttp.ClientSession() as session:
                supply = await _get_token_supply(session, mint)
                if not supply or supply <= 0:
                    return out
                res = await _identify_dev_wallet(session, mint, supply)
                if not res:
                    return out
                current_addr, current_pct = res
                if not base or base.get("dev_wallet_addr") != current_addr:
                    # First sighting OR dev wallet changed → new baseline
                    baselines[key] = {
                        "dev_wallet_addr": current_addr,
                        "baseline_pct_supply": round(current_pct, 4),
                        "baseline_ts": now,
                        "last_seen_ts": now,
                        "last_pct_supply": round(current_pct, 4),
                    }
                    base = baselines[key]
                else:
                    base["last_seen_ts"] = now
                    base["last_pct_supply"] = round(current_pct, 4)

                baseline_pct = float(base.get("baseline_pct_supply") or 0)
                dumped = max(0.0, baseline_pct - current_pct)
                out = {
                    "dev_wallet_addr": current_addr,
                    "dev_pct_remaining": round(current_pct, 4),
                    "dev_baseline_pct": round(baseline_pct, 4),
                    "dev_pct_dumped": round(dumped, 4),
                    "dev_balance_change_pct": round(
                        ((current_pct - baseline_pct) / baseline_pct * 100)
                        if baseline_pct > 0 else 0.0,
                        3,
                    ),
                    "dev_baseline_age_hours": round(
                        (now - float(base.get("baseline_ts") or now)) / 3600.0,
                        2,
                    ),
                    "dev_features_source": "rpc",
                }
    except Exception as e:
        logger.debug(f"[DevWallet] fetch error for {mint[:8]}: {e}")
    return out


def prune_baselines(
    baselines: Dict[str, Dict[str, Any]], max_age_days: float = 7.0
) -> int:
    """Remove baselines unseen for max_age_days. Returns count removed."""
    cutoff = time.time() - max_age_days * 86400
    to_remove = [
        k for k, v in baselines.items()
        if float(v.get("last_seen_ts") or 0) < cutoff
    ]
    for k in to_remove:
        del baselines[k]
    return len(to_remove)


# Convenience: in-memory cache shared by scanner. Loaded once at scanner
# startup, periodically saved.
class DevWalletTracker:
    """Owns the persistent state. One instance per dip_scanner."""

    def __init__(self, save_every_n_updates: int = 20):
        self._baselines = _load_baselines()
        self._updates_since_save = 0
        self._save_every = save_every_n_updates
        # Per-cycle cold-refresh budget (reset at each scan cycle start via
        # reset_cycle()). Bounds Solana RPC fan-out on a cold-universe cycle.
        self._refresh_this_cycle = 0
        # Periodic prune at startup
        removed = prune_baselines(self._baselines)
        if removed:
            logger.info(f"[DevWallet] Pruned {removed} stale baselines on startup")
        logger.info(f"[DevWallet] Loaded {len(self._baselines)} baselines")

    def reset_cycle(self) -> None:
        """Reset the per-cycle cold-refresh counter. Call at scan-cycle start."""
        self._refresh_this_cycle = 0

    def _refresh_allowed(self) -> bool:
        """Token-bucket: True if a cold/stale token may spend an RPC refresh
        this cycle. Consumes one unit of budget on each True. Fail-open: a
        non-positive cap means unbounded (back-compat)."""
        cap = _max_refresh_per_cycle()
        if cap <= 0:
            return True
        if self._refresh_this_cycle >= cap:
            return False
        self._refresh_this_cycle += 1
        return True

    async def get_features(self, mint: str, cache_only: bool = False) -> Dict[str, Any]:
        # Only the live RPC path (not cache_only) is gated by the per-cycle cap.
        refresh_allowed = None if cache_only else self._refresh_allowed
        feats = await fetch_dev_features(
            mint, self._baselines, cache_only=cache_only,
            refresh_allowed=refresh_allowed,
        )
        if feats:
            self._updates_since_save += 1
            if self._updates_since_save >= self._save_every:
                _save_baselines(self._baselines)
                self._updates_since_save = 0
        return feats

    def flush(self) -> None:
        _save_baselines(self._baselines)
