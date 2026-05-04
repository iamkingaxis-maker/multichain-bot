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
# at a time. Public RPC bursts trigger 429 on this method. Per-token
# baseline check throttling: skip RPC if baseline_age < 300s.
_RPC_SEMAPHORE: Optional[asyncio.Semaphore] = None
_BASELINE_REFRESH_MIN_AGE_SECS = 300.0

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
            timeout=aiohttp.ClientTimeout(total=8)
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
    # Walk top 10 — typical layout has LP first, then dev/treasury
    for acc in accounts[:10]:
        ui_amount = acc.get("uiAmount") or 0
        addr = acc.get("address")
        if not addr or ui_amount <= 0:
            continue
        owner = await _resolve_owner(session, addr)
        if not owner:
            continue
        if owner in KNOWN_PROGRAMS:
            continue
        pct = (ui_amount / total_supply) * 100.0
        return (owner, pct)
    return None


async def fetch_dev_features(
    mint: str, baselines: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Compute dev-wallet features for a token. Updates `baselines` in place
    when a new token is first seen. Returns dict suitable for entry_meta.

    Throttling: if a baseline exists and is <5 min old, returns cached
    values without making an RPC call. This bounds RPC pressure to once
    per token per 5 minutes — important on public RPC which rate-limits
    getTokenLargestAccounts.
    """
    global _RPC_SEMAPHORE
    if _RPC_SEMAPHORE is None:
        _RPC_SEMAPHORE = asyncio.Semaphore(1)
    out: Dict[str, Any] = {}
    if not mint:
        return out
    key = mint  # don't lowercase Solana addrs (they're case-sensitive)
    now = time.time()

    # Fast path: recent baseline → use cached values, no RPC
    base = baselines.get(key)
    if base and (now - float(base.get("last_seen_ts") or 0)) < _BASELINE_REFRESH_MIN_AGE_SECS:
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
        # Periodic prune at startup
        removed = prune_baselines(self._baselines)
        if removed:
            logger.info(f"[DevWallet] Pruned {removed} stale baselines on startup")
        logger.info(f"[DevWallet] Loaded {len(self._baselines)} baselines")

    async def get_features(self, mint: str) -> Dict[str, Any]:
        feats = await fetch_dev_features(mint, self._baselines)
        if feats:
            self._updates_since_save += 1
            if self._updates_since_save >= self._save_every:
                _save_baselines(self._baselines)
                self._updates_since_save = 0
        return feats

    def flush(self) -> None:
        _save_baselines(self._baselines)
