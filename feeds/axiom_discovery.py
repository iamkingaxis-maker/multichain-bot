"""
Shared Axiom token discovery.

Hits Axiom's /users-trending-v2 endpoint (authenticated) and returns
pairs in DexScreener-like format so any consumer can merge them with
DexScreener results without extra translation.

Path:
  1. Direct call to api2/3/4.axiom.trade (fast when Railway IP is allowed).
  2. If direct returns 5xx (Cloudflare block), fall back to the Worker REST
     proxy at ${AXIOM_REFRESH_RELAY_URL}/rest-proxy — the Worker runs on
     Cloudflare's edge, so Axiom's WAF sees another CF node and lets it
     through.
"""
import logging
import os
from typing import List, Optional

import aiohttp

logger = logging.getLogger(__name__)

_AXIOM_SERVERS = (
    "https://api2.axiom.trade",
    "https://api3.axiom.trade",
    "https://api4.axiom.trade",
)
_HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://axiom.trade",
    "Referer": "https://axiom.trade/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
}


async def fetch_axiom_trending_pairs(
    auth_manager,
    time_period: str = "1h",
    timeout_s: float = 8.0,
) -> List[dict]:
    """
    Fetch Solana trending tokens from Axiom users-trending-v2.
    Returns DexScreener-style pair dicts. Empty list on any failure.
    """
    token = _extract_auth_token(auth_manager)
    if not token:
        logger.info("[AxiomDiscovery] no auth token — returning empty")
        return []

    path = f"/users-trending-v2?timePeriod={time_period}"
    cookie = f"auth-access-token={token}"
    headers = {**_HEADERS_BASE, "Cookie": cookie}

    last_status = None
    async with aiohttp.ClientSession() as session:
        for server in _AXIOM_SERVERS:
            try:
                async with session.get(
                    f"{server}{path}", headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout_s),
                ) as resp:
                    last_status = resp.status
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        raw = data if isinstance(data, list) else (data.get("pairs") or [])
                        out = _normalize(raw)
                        logger.info(
                            "[AxiomDiscovery] direct %s: %d raw / %d normalized",
                            server.split("//")[-1], len(raw), len(out),
                        )
                        return out
                    if resp.status in (401, 403):
                        logger.info(
                            "[AxiomDiscovery] direct auth rejected (%s) at %s",
                            resp.status, server,
                        )
                        return []
                    logger.info(
                            "[AxiomDiscovery] direct %s returned HTTP %s",
                            server, resp.status,
                    )
            except Exception as e:
                logger.info("[AxiomDiscovery] direct %s failed: %s", server, e)
                continue

    logger.info(
        "[AxiomDiscovery] direct all failed (last=%s) — trying Worker proxy",
        last_status,
    )
    return await _fetch_via_worker(path, cookie, timeout_s)


async def _fetch_via_worker(
    path: str, cookie: str, timeout_s: float,
) -> List[dict]:
    relay_url = os.environ.get("AXIOM_REFRESH_RELAY_URL", "").strip()
    relay_secret = os.environ.get("AXIOM_REFRESH_RELAY_SECRET", "").strip()
    if not relay_url or not relay_secret:
        logger.info("[AxiomDiscovery] Worker proxy not configured — returning empty")
        return []

    # Strip any path from the relay URL — we need just the origin so we can
    # append /rest-proxy regardless of whether env var was set to /refresh.
    try:
        from urllib.parse import urlparse
        parsed = urlparse(relay_url)
        worker_origin = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        worker_origin = relay_url.rstrip("/")
    proxy_url = f"{worker_origin}/rest-proxy"

    payload = {
        "secret": relay_secret,
        "path": path,
        "cookie": cookie,
        "server": "https://api3.axiom.trade",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                proxy_url, json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout_s + 4),
            ) as resp:
                status = resp.status
                if status != 200:
                    logger.info(
                        "[AxiomDiscovery] Worker proxy HTTP %s for %s",
                        status, path,
                    )
                    return []
                data = await resp.json(content_type=None)
    except Exception as e:
        logger.info("[AxiomDiscovery] Worker proxy error: %s", e)
        return []

    raw = data if isinstance(data, list) else (data.get("pairs") or [])
    out = _normalize(raw)
    if raw and not out:
        first = raw[0]
        if isinstance(first, dict):
            summary = f"dict keys={sorted(first.keys())[:25]}"
        else:
            preview = repr(first)[:200]
            summary = f"type={type(first).__name__} preview={preview}"
        # Also show top-level data shape so we can tell if it's wrapped
        if isinstance(data, dict):
            summary += f" | data keys={list(data.keys())[:10]}"
        logger.info(
            "[AxiomDiscovery] Worker proxy: %d raw / 0 normalized — %s",
            len(raw), summary,
        )
    else:
        logger.info(
            "[AxiomDiscovery] Worker proxy: %d raw / %d normalized",
            len(raw), len(out),
        )
    return out


def _extract_auth_token(auth_manager) -> Optional[str]:
    if auth_manager is None:
        return None
    tok = getattr(auth_manager, "auth_token", None)
    if isinstance(tok, str) and tok:
        return tok
    return None


def _normalize(pairs: list) -> List[dict]:
    """Convert Axiom records to DexScreener-style pair dicts."""
    out: List[dict] = []
    for p in pairs:
        try:
            addr = (
                p.get("mint")
                or p.get("address")
                or p.get("tokenAddress")
                or ""
            )
            if not addr or addr.startswith("0x"):
                continue
            symbol = p.get("symbol") or p.get("ticker") or "?"
            price = float(p.get("priceUsd") or p.get("price") or 0)
            liq = float(p.get("liquidityUsd") or p.get("liquidity") or 0)
            mc = float(p.get("marketCap") or 0)
            ch24 = float(p.get("priceChange24h") or p.get("change24h") or 0)
            ch6 = float(p.get("priceChange6h") or p.get("change6h") or 0)
            ch1 = float(p.get("priceChange1h") or p.get("change1h") or 0)
            ch5 = float(p.get("priceChange5m") or p.get("change5m") or 0)
            vol_h1 = float(p.get("volumeH1") or p.get("volume1h") or 0)
            vol_m5 = float(p.get("volumeM5") or p.get("volume5m") or 0)
            buys_m5 = int(p.get("buysM5") or p.get("buys5m") or 0)
            sells_m5 = int(p.get("sellsM5") or p.get("sells5m") or 0)
            created = int(p.get("pairCreatedAt") or p.get("createdAt") or 0)
            pair_addr = (
                p.get("pairAddress")
                or p.get("poolAddress")
                or p.get("pool_address")
                or ""
            )

            out.append({
                "chainId": "solana",
                "baseToken": {"address": addr, "symbol": symbol},
                "pairAddress": pair_addr,
                "priceUsd": str(price),
                "marketCap": mc,
                "liquidity": {"usd": liq},
                "priceChange": {
                    "m5": ch5, "h1": ch1, "h6": ch6, "h24": ch24,
                },
                "volume": {"m5": vol_m5, "h1": vol_h1},
                "txns": {"m5": {"buys": buys_m5, "sells": sells_m5}},
                "pairCreatedAt": created,
                "_source": "axiom",
            })
        except Exception:
            continue
    return out
