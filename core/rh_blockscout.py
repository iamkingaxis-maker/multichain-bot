# core/rh_blockscout.py
"""RH-chain (Robinhood Chain) Blockscout explorer client — the CHEAP holder /
rug-structure data source that SHADOWS the eth_getLogs reconstruction in
core/rh_rug_signals.py (2026-07-12, AxiS-approved).

WHY: compute_entry_stamp reconstructs the holder map by replaying every ERC20
Transfer log (40-60 paced eth_getLogs / up to 90s per token). Blockscout serves
the SAME facts — holder ranking, total supply, holders_count — precomputed, for
1-2 keyless HTTP calls (~1s). This module wraps that API and derives the exact
same feature set (top1/top10/shoulder/hidden-supply/burn/pool share) so the two
sources can be graded side-by-side in the ledger. Until agreement is confirmed
these land as `bs_`-prefixed SHADOW fields ALONGSIDE the reconstruction — nothing
here replaces it, and nothing here gates an entry.

API (confirmed live 2026-07-12, free, no key):
  GET /api/v2/tokens/{addr}
      -> holders_count, total_supply, decimals, volume_24h,
         circulating_market_cap, exchange_rate, reputation, symbol, name
  GET /api/v2/tokens/{addr}/holders
      -> items:[{address:{hash,is_contract,is_scam,reputation,metadata.tags},
                 value}]  (sorted by value DESC; 0x00..dEaD burn near the top)

FAIL-OPEN CONTRACT: every public entrypoint returns {} / a bs_source_ok=False
stamp on ANY error (timeout, non-200, malformed JSON, bad supply). Nothing here
ever raises into the rug stamper — the shadow stamp must never block, delay, or
crash a paper fill. 10s timeout, hard 10-min per-token cache (the lane stamps
many tokens; one call set per token per window).

The distribution math MIRRORS the Solana definitions in core/holder_features.py
so the cross-chain rug-gate math stays consistent:
  hidden_supply_share_pct = 100 - pool_pct - top10_pct  (Solana subtracts an
  insider_pct term too; Blockscout has no insider flag, so that term is 0 and
  the formula collapses to exactly the eth_getLogs visible_float_pct — which is
  precisely the number the grader compares against).
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

# ── endpoint / knobs ─────────────────────────────────────────────────────────
BASE = os.environ.get(
    "RH_BLOCKSCOUT_BASE", "https://robinhoodchain.blockscout.com").rstrip("/")
TIMEOUT_S = 10.0
CACHE_TTL_S = 600.0            # per-token 10-min hard cache (rate-polite)
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

DEAD_ADDR = "0x000000000000000000000000000000000000dead"
ZERO_ADDR = "0x" + "0" * 40
_BURN_ADDRS = {DEAD_ADDR, ZERO_ADDR}
# holder metadata tag slugs that identify a pool/LP contract (mirror the
# _LP_TAGS intent in core/holder_features.py).
_POOL_TAG_SLUGS = {"lp", "amm", "pool", "liquidity", "dex", "uniswap",
                   "liquidity-pool"}

_CACHE: dict = {}                 # token_addr -> (computed_ts, stamp_dict)
_CACHE_LOCK = threading.Lock()

# the bs_ field set (stable key order for the grader / ledger schema)
_NULL_STAMP = {
    "bs_source_ok": False,
    "bs_holders_count": None,
    "bs_reputation": None,
    "bs_total_supply": None,
    "bs_mcap": None,
    "bs_volume_24h": None,
    "bs_top1_pct": None,
    "bs_top10_pct": None,
    "bs_shoulder_11_20_pct": None,
    "bs_hidden_supply_share_pct": None,
    "bs_pool_pct": None,
    "bs_burn_pct": None,
    "bs_n_scam": None,
    "bs_n_holders_ranked": None,   # holders on the page we scored (<= page cap)
}


# ── HTTP chokepoint (the ONE place network happens; tests monkeypatch this) ──
def _get_json(path: str):
    """GET BASE+path -> parsed JSON. Raises on any transport/HTTP/JSON error;
    every caller wraps this and fails open."""
    req = urllib.request.Request(BASE + path, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.load(r)


# ── pure coercers ────────────────────────────────────────────────────────────
def _to_int(v) -> int:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return 0


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_pool_tags(tags) -> bool:
    for t in tags or []:
        s = str(t).lower()
        if s in _POOL_TAG_SLUGS or "liquid" in s or "pool" in s:
            return True
    return False


# ── fetch: token meta (1 call) ───────────────────────────────────────────────
def fetch_token_meta(addr: str) -> dict:
    """GET /api/v2/tokens/{addr} -> normalized meta dict, or {} on any error.
    NEVER raises."""
    try:
        m = _get_json(f"/api/v2/tokens/{addr.lower()}")
    except Exception:
        return {}
    if not isinstance(m, dict):
        return {}
    return {
        "holders_count": _to_int(m.get("holders_count")) or None,
        "total_supply": _to_int(m.get("total_supply")) or None,
        "decimals": _to_int(m.get("decimals")) or None,
        "volume_24h": _to_float(m.get("volume_24h")),
        "mcap": _to_float(m.get("circulating_market_cap")),
        "reputation": m.get("reputation"),
        "exchange_rate": _to_float(m.get("exchange_rate")),
        "symbol": m.get("symbol"),
        "name": m.get("name"),
    }


# ── pure: holders-page rows -> normalized list ───────────────────────────────
def normalize_holder_rows(items) -> list:
    """Blockscout holders `items` -> [{addr,value,is_contract,is_scam,tags}].
    Pure; skips undecodable rows. addr lowercased."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        a = it.get("address") or {}
        if not isinstance(a, dict):
            continue
        h = str(a.get("hash") or "").lower()
        if not h:
            continue
        tags = [t.get("slug") for t in ((a.get("metadata") or {}).get("tags") or [])
                if isinstance(t, dict)]
        out.append({
            "addr": h,
            "value": _to_int(it.get("value")),
            "is_contract": bool(a.get("is_contract")),
            "is_scam": bool(a.get("is_scam")),
            "reputation": a.get("reputation"),
            "tags": tags,
        })
    return out


# ── pure: distribution features (mirrors core/holder_features.py defs) ────────
def compute_distribution(rows: list, total_supply: Optional[int],
                         pool_addr: Optional[str] = None) -> Optional[dict]:
    """Normalized holder rows + on-chain total_supply -> the hidden-supply
    feature set. Pure; returns None when supply is non-positive (nothing to
    normalize by).

    Classification (mirror Solana): BURN addresses (0x00..dEaD / 0x0) and POOL
    holders (the known pool addr, any is_contract holder, or a pool-tagged
    holder) are held OUT of the `real` holder ranking — top1/top10/shoulder are
    computed over real holders only, exactly like holder_features excludes the
    LP vaults + insiders. hidden_supply_share_pct = 100 - pool_pct - top10_pct
    (Solana's insider term is 0 on EVM), which is identical in definition to the
    eth_getLogs visible_float_pct the grader compares against."""
    if not total_supply or total_supply <= 0:
        return None
    pool_set = {pool_addr.lower()} if pool_addr else set()
    burn_val = 0
    pool_val = 0
    n_scam = 0
    real_vals = []
    for r in rows:
        if r["is_scam"]:
            n_scam += 1
        v = r["value"]
        if v <= 0:
            continue
        if r["addr"] in _BURN_ADDRS:
            burn_val += v
            continue
        if (r["addr"] in pool_set or r["is_contract"]
                or _is_pool_tags(r["tags"])):
            pool_val += v
            continue
        real_vals.append(v)
    real_vals.sort(reverse=True)

    def pct(x) -> float:
        return round(x / total_supply * 100.0, 2)

    top1 = pct(real_vals[0]) if real_vals else 0.0
    top10 = pct(sum(real_vals[:10]))
    shoulder = pct(sum(real_vals[10:20]))
    pool_pct = pct(pool_val)
    burn_pct = pct(burn_val)
    hidden = round(max(0.0, 100.0 - pool_pct - top10), 2)
    return {
        "top1_pct": top1,
        "top10_pct": top10,
        "shoulder_11_20_pct": shoulder,
        "pool_pct": pool_pct,
        "burn_pct": burn_pct,
        "hidden_supply_share_pct": hidden,
        "n_scam_flagged_holders": n_scam,
        "n_holders_ranked": len(real_vals),
    }


def fetch_holder_distribution(addr: str,
                              pool_addr: Optional[str] = None,
                              total_supply: Optional[int] = None) -> dict:
    """GET /api/v2/tokens/{addr}/holders -> distribution features, or {} on any
    error. NEVER raises. total_supply may be passed in (from fetch_token_meta)
    to save a call; if omitted it is fetched here."""
    if total_supply is None:
        meta = fetch_token_meta(addr)
        total_supply = meta.get("total_supply")
    try:
        h = _get_json(f"/api/v2/tokens/{addr.lower()}/holders")
    except Exception:
        return {}
    if not isinstance(h, dict):
        return {}
    rows = normalize_holder_rows(h.get("items"))
    dist = compute_distribution(rows, total_supply, pool_addr=pool_addr)
    return dist or {}


# ── the SHADOW stamp: bs_-prefixed fields for the ledger row (cached) ─────────
def _build_stamp(token: str, pool_addr: Optional[str]) -> dict:
    """One call set (meta + holders) -> the full bs_ stamp. Fail-open: any
    partial failure yields the null stamp for the missing tier; bs_source_ok is
    True only when supply + a scored holder page were both obtained."""
    meta = fetch_token_meta(token)
    total_supply = meta.get("total_supply")
    stamp = dict(_NULL_STAMP)
    stamp["bs_holders_count"] = meta.get("holders_count")
    stamp["bs_reputation"] = meta.get("reputation")
    stamp["bs_total_supply"] = (str(total_supply)
                                if total_supply is not None else None)
    stamp["bs_mcap"] = meta.get("mcap")
    stamp["bs_volume_24h"] = meta.get("volume_24h")
    if not total_supply or total_supply <= 0:
        return stamp
    dist = fetch_holder_distribution(token, pool_addr=pool_addr,
                                     total_supply=total_supply)
    if not dist:
        return stamp
    stamp.update({
        "bs_source_ok": True,
        "bs_top1_pct": dist.get("top1_pct"),
        "bs_top10_pct": dist.get("top10_pct"),
        "bs_shoulder_11_20_pct": dist.get("shoulder_11_20_pct"),
        "bs_hidden_supply_share_pct": dist.get("hidden_supply_share_pct"),
        "bs_pool_pct": dist.get("pool_pct"),
        "bs_burn_pct": dist.get("burn_pct"),
        "bs_n_scam": dist.get("n_scam_flagged_holders"),
        "bs_n_holders_ranked": dist.get("n_holders_ranked"),
    })
    return stamp


def blockscout_stamp(token: str, pool_addr: Optional[str] = None,
                     use_cache: bool = True) -> dict:
    """The public entrypoint the rug stamper calls. Returns the bs_-prefixed
    SHADOW field dict for `token` (always the full key set — null fields on
    failure). NEVER raises. Hard 10-min per-token cache so a re-stamped pool
    costs zero network."""
    key = str(token or "").lower()
    if not key:
        return dict(_NULL_STAMP)
    now = time.time()
    if use_cache:
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit and (now - hit[0]) < CACHE_TTL_S:
                return dict(hit[1])
    try:
        stamp = _build_stamp(key, pool_addr)
    except Exception:
        stamp = dict(_NULL_STAMP)
    if use_cache:
        with _CACHE_LOCK:
            _CACHE[key] = (now, dict(stamp))
    return stamp


def clear_cache() -> None:
    """Test/maintenance hook — drop the per-token cache."""
    with _CACHE_LOCK:
        _CACHE.clear()
