"""
Shared Axiom token discovery.

Hits Axiom's /users-trending-v2 endpoint (authenticated) and returns
pairs in DexScreener-like format so any consumer can merge them with
DexScreener results without extra translation.

Axiom's public /meme-trending endpoint was removed (all servers 404),
so users-trending-v2 is the only working REST discovery source.
"""
import logging
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
        return []

    headers = {**_HEADERS_BASE, "Cookie": f"auth-access-token={token}"}

    for server in _AXIOM_SERVERS:
        url = f"{server}/users-trending-v2?timePeriod={time_period}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout_s),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        raw = data if isinstance(data, list) else (data.get("pairs") or [])
                        return _normalize(raw)
                    if resp.status in (401, 403):
                        logger.debug("[AxiomDiscovery] auth rejected (%s)", resp.status)
                        return []
        except Exception as e:
            logger.debug("[AxiomDiscovery] %s failed: %s", server, e)
            continue
    return []


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
