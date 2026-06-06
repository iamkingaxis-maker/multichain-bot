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


def _rows(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("kols", "data", "wallets", "result", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


async def probe_vision_kols(auth_manager) -> dict:
    """Pull vision-kols-v2 (top traders) and summarize the shape for inspection."""
    data = await _authed_get(auth_manager, "/vision-kols-v2?v=1")
    out = {"endpoint": "vision-kols-v2"}
    if isinstance(data, dict) and data.get("error"):
        return {**out, **data}
    rows = _rows(data)
    out["count"] = len(rows)
    out["top_level_type"] = type(data).__name__
    if isinstance(data, dict):
        out["top_level_keys"] = sorted(data.keys())[:20]
    if rows and isinstance(rows[0], dict):
        out["row_keys"] = sorted(rows[0].keys())
        out["sample"] = [
            {k: rows[i].get(k) for k in list(rows[i].keys())[:12]}
            for i in range(min(5, len(rows)))
        ]
    return out
