"""One-shot feasibility probe for the copy-trade frontier (2026-06-06).

Pulls Axiom Vision's top-trader (KOL) feed + a sample of one KOL's transactions to
confirm the data source before building the full collector. READ-ONLY. Mirrors
feeds/axiom_discovery auth exactly: cookie auth (auth-access-token) + direct
api2/3/4 with a Cloudflare-relay (/rest-proxy) fallback.

Endpoints discovered from the Vision page network trace:
  GET  /vision-kols-v2                  -> curated top traders (KOLs)
  POST /tracked-wallet-transactions-v3  -> their trades (probed separately later)
"""
import os
import logging
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

_SERVERS = (
    "https://api2.axiom.trade",
    "https://api3.axiom.trade",
    "https://api4.axiom.trade",
)
_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://axiom.trade",
    "Referer": "https://axiom.trade/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}


async def _authed_get(auth_manager, path: str, timeout_s: float = 8.0):
    """Authed GET of an arbitrary Axiom REST path -> raw JSON, or {'error': ...}."""
    if hasattr(auth_manager, "ensure_valid_token"):
        try:
            await auth_manager.ensure_valid_token()
        except Exception:
            pass
    try:
        from feeds.axiom_discovery import _extract_auth_token
        token = _extract_auth_token(auth_manager)
    except Exception as e:
        return {"error": f"token_extract_{type(e).__name__}"}
    if not token:
        return {"error": "no_token"}
    cookie = f"auth-access-token={token}"
    headers = {**_HEADERS, "Cookie": cookie}

    async with aiohttp.ClientSession() as s:
        for srv in _SERVERS:
            try:
                async with s.get(
                    f"{srv}{path}", headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout_s),
                ) as r:
                    if r.status == 200:
                        return await r.json(content_type=None)
                    if r.status in (401, 403):
                        return {"error": f"auth_{r.status}"}
            except Exception:
                continue

    # Cloudflare-relay fallback (same as axiom_discovery)
    relay = os.environ.get("AXIOM_REFRESH_RELAY_URL", "").strip()
    secret = os.environ.get("AXIOM_REFRESH_RELAY_SECRET", "").strip()
    if relay and secret:
        p = urlparse(relay)
        origin = f"{p.scheme}://{p.netloc}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{origin}/rest-proxy",
                    json={"secret": secret, "path": path, "cookie": cookie,
                          "server": "https://api3.axiom.trade"},
                    timeout=aiohttp.ClientTimeout(total=timeout_s + 4),
                ) as r:
                    if r.status == 200:
                        return await r.json(content_type=None)
                    return {"error": f"relay_{r.status}"}
        except Exception as e:
            return {"error": f"relay_exc_{type(e).__name__}"}
    return {"error": "all_failed_no_relay"}


async def probe_kol_trades(auth_manager, wallet: str) -> dict:
    """Confirm we can pull ONE wallet's trades/entries. Tries (1) the client's
    get_user_portfolio, (2) the raw tracked-wallet-transactions-v3 POST with body
    guesses. Returns the shape of whatever works -> tells us the collector's path."""
    import asyncio
    out = {"wallet": wallet, "tried": []}
    # (1) client.get_user_portfolio
    try:
        client = auth_manager.get_client() if hasattr(auth_manager, "get_client") else None
        if client is not None and hasattr(client, "get_user_portfolio"):
            res = client.get_user_portfolio(wallet)
            if asyncio.iscoroutine(res):
                res = await res
            if isinstance(res, dict):
                out["get_user_portfolio"] = {"type": "dict", "keys": sorted(res.keys())[:25]}
            elif isinstance(res, list):
                out["get_user_portfolio"] = {"type": "list", "len": len(res),
                                             "row_keys": sorted(res[0].keys())[:25] if res and isinstance(res[0], dict) else None}
            else:
                out["get_user_portfolio"] = {"type": type(res).__name__, "preview": repr(res)[:200]}
        else:
            out["tried"].append("get_user_portfolio: not available on client")
    except Exception as e:
        out["get_user_portfolio_error"] = f"{type(e).__name__}: {e}"
    # (2) raw tracked-wallet-transactions-v3 POST body guesses
    for body in ({"walletAddresses": [wallet]}, {"wallets": [wallet]}, {"walletAddress": wallet}):
        r = await _authed_post(auth_manager, "/tracked-wallet-transactions-v3", body)
        key = "txn_" + "_".join(body.keys())
        if isinstance(r, dict) and r.get("error"):
            out[key] = r["error"]
        else:
            rows = _rows(r)
            out[key] = {"rows": len(rows), "row_keys": sorted(rows[0].keys())[:25] if rows and isinstance(rows[0], dict) else (sorted(r.keys())[:20] if isinstance(r, dict) else type(r).__name__)}
            break  # found a working body
    return out


async def _authed_post(auth_manager, path: str, body: dict, timeout_s: float = 10.0):
    """Authed POST of an Axiom REST path (direct only; relay is GET-only)."""
    if hasattr(auth_manager, "ensure_valid_token"):
        try:
            await auth_manager.ensure_valid_token()
        except Exception:
            pass
    try:
        from feeds.axiom_discovery import _extract_auth_token
        token = _extract_auth_token(auth_manager)
    except Exception as e:
        return {"error": f"token_{type(e).__name__}"}
    if not token:
        return {"error": "no_token"}
    headers = {**_HEADERS, "Cookie": f"auth-access-token={token}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as s:
        for srv in _SERVERS:
            try:
                async with s.post(f"{srv}{path}", headers=headers, json=body,
                                  timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
                    if r.status == 200:
                        return await r.json(content_type=None)
                    if r.status in (401, 403):
                        return {"error": f"auth_{r.status}"}
            except Exception:
                continue
    return {"error": "post_all_failed"}


def _rows(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("kols", "data", "wallets", "result", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


def _stat(row, window, key):
    try:
        return float(((row.get("stats") or {}).get(window) or {}).get(key))
    except (TypeError, ValueError):
        return None


def _wr(row, window):
    s = (row.get("stats") or {}).get(window) or {}
    w = s.get("totalWinningPositions"); l = s.get("totalLosingPositions")
    try:
        w = float(w); l = float(l)
        return round(100 * w / (w + l), 1) if (w + l) > 0 else None
    except (TypeError, ValueError):
        return None


async def probe_vision_kols(auth_manager) -> dict:
    """Pull vision-kols-v2 (top traders), rank by REAL profitability (the KOL label is
    not a profit filter — many are net-negative), and surface the followable subset."""
    data = await _authed_get(auth_manager, "/vision-kols-v2?v=1")
    out = {"endpoint": "vision-kols-v2"}
    if isinstance(data, dict) and data.get("error"):
        return {**out, **data}
    rows = _rows(data)
    out["count"] = len(rows)
    # self-select the profitable ones (7d AND 30d PnL > 0)
    profitable = [r for r in rows
                  if (_stat(r, "sevenDayStats", "totalPnlUsd") or 0) > 0
                  and (_stat(r, "thirtyDayStats", "totalPnlUsd") or 0) > 0]
    out["net_positive_7d"] = sum(1 for r in rows if (_stat(r, "sevenDayStats", "totalPnlUsd") or 0) > 0)
    out["net_positive_30d"] = sum(1 for r in rows if (_stat(r, "thirtyDayStats", "totalPnlUsd") or 0) > 0)
    out["profitable_7d_and_30d"] = len(profitable)
    profitable.sort(key=lambda r: _stat(r, "sevenDayStats", "totalPnlUsd") or 0, reverse=True)
    out["top_followable"] = [{
        "name": r.get("name"),
        "wallet": r.get("walletAddress"),
        "pnl_7d_usd": round(_stat(r, "sevenDayStats", "totalPnlUsd") or 0),
        "pnl_30d_usd": round(_stat(r, "thirtyDayStats", "totalPnlUsd") or 0),
        "wr_7d": _wr(r, "sevenDayStats"),
        "closed_7d": int(_stat(r, "sevenDayStats", "totalClosedPositions") or 0),
        "avg_hold_min_7d": round((_stat(r, "sevenDayStats", "totalHoldTimeMs") or 0)
                                 / max(1, _stat(r, "sevenDayStats", "totalClosedPositions") or 1) / 60000, 1),
    } for r in profitable[:20]]
    return out
