"""
DexScreener internal-API OHLCV client.

Drop-in OHLCV provider that fetches candle data from DexScreener's internal
chart endpoint (`io.dexscreener.com/dex/chart/...`). Returns Candle objects
with the same shape as GeckoTerminalClient.

Why: GeckoTerminal free tier (30 req/min) is the bottleneck for chart_data
coverage (currently ~6-20%). DexScreener's internal API is undocumented but
much higher capacity in practice. We hit it directly, parse the binary
response (see dexscreener_chart_format.py), and serve candles.

Cloudflare protection: the io.dexscreener.com endpoint requires a Chrome-
class TLS fingerprint. We use curl_cffi with impersonate='chrome' which
bypasses this. Direct curl/requests gets a 403 challenge page.

DEX slug discovery: io.dexscreener.com URLs require a per-DEX slug
(`solamm` for Raydium AMM, `pumpfundex` for PumpSwap, etc). The mapping
isn't documented; we discover it once per pool by hitting the public
`api.dexscreener.com/latest/dex/pairs/solana/{pair}` endpoint to read the
`dexId`, then map dexId → io-slug via SLUG_MAP. Cached per pool.

Note: this is a complement to (not a replacement for) GeckoTerminalClient.
The full bot uses both, with DexScreener as the high-throughput primary
and GT as the fallback.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from feeds.candle_utils import Candle
from feeds.dexscreener_chart_format import parse_chart_bars

logger = logging.getLogger(__name__)

_DEXS_BASE = "https://io.dexscreener.com"
_DEXS_PUBLIC = "https://api.dexscreener.com/latest/dex"

# DexScreener public dexId → io.dexscreener internal slug mapping.
# Discovered via network inspection on dexscreener.com pair pages.
# Add new mappings as the bot encounters new DEX types.
_SLUG_MAP: Dict[str, str] = {
    "raydium": "solamm",
    "pumpswap": "pumpfundex",
    "pumpfun": "pumpfundex",  # alternate naming on some pairs
    # TODO when encountered: orca, meteora, openbookv2, etc.
}

_QUOTE_SOL = "So11111111111111111111111111111111111111112"
_QUOTE_USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# Resolution mapping for DexScreener `res` URL param.
_RES_MAP = {
    1: "1",       # 1-minute
    5: "5",       # 5-minute
    15: "15",     # 15-minute
    60: "60",     # 60-minute (1h)
    240: "240",   # 4h
}


class DexScreenerClient:
    """Mirrors GeckoTerminalClient's fetch_5m / fetch_1m / etc interface,
    but goes via DexScreener's internal binary chart API.

    Thread-bridges curl_cffi (sync, Chrome-impersonating) into asyncio via
    asyncio.to_thread so we don't block the event loop.
    """

    def __init__(
        self,
        cache_ttl: int = 60,
        rate_per_min: int = 90,
    ):
        self._cache_ttl = cache_ttl
        self._cache: Dict[str, Tuple[float, List[Candle]]] = {}
        self._slug_cache: Dict[str, str] = {}
        self._quote_cache: Dict[str, str] = {}
        self._rate_per_min = rate_per_min
        self._request_log: List[float] = []
        self._lock = asyncio.Lock()
        self._session = None  # lazy-init curl_cffi session

    def _ensure_session(self):
        """Lazy-init curl_cffi session — keeps a single persistent connection."""
        if self._session is None:
            try:
                from curl_cffi import requests as cf_requests
            except ImportError as e:
                raise RuntimeError(
                    "curl_cffi not installed. Install with: pip install curl_cffi"
                ) from e
            self._session = cf_requests.Session(impersonate="chrome")
        return self._session

    async def _throttle(self, now: float):
        cutoff = now - 60.0
        self._request_log = [t for t in self._request_log if t > cutoff]
        if len(self._request_log) >= self._rate_per_min:
            sleep_s = 60.0 - (now - self._request_log[0]) + 0.5
            logger.debug(f"[DexScreener] rate-limit sleep {sleep_s:.2f}s")
            await asyncio.sleep(max(0.0, sleep_s))
        self._request_log.append(time.monotonic())

    async def _resolve_pool_meta(self, pair_address: str) -> Tuple[Optional[str], Optional[str]]:
        """Resolve (dex_slug, quote_token_mint) for a pair. Cached per pool."""
        cached_slug = self._slug_cache.get(pair_address)
        cached_q = self._quote_cache.get(pair_address)
        if cached_slug and cached_q:
            return cached_slug, cached_q

        url = f"{_DEXS_PUBLIC}/pairs/solana/{pair_address}"
        try:
            sess = self._ensure_session()
            resp = await asyncio.to_thread(sess.get, url, timeout=10)
            if resp.status_code != 200:
                logger.debug(f"[DexScreener] meta {pair_address[:12]}: HTTP {resp.status_code}")
                return None, None
            data = resp.json()
        except Exception as e:
            logger.debug(f"[DexScreener] meta error {pair_address[:12]}: {e}")
            return None, None

        pairs = data.get("pairs") or data.get("pair") or []
        if isinstance(pairs, dict):
            pairs = [pairs]
        if not pairs:
            return None, None
        p = pairs[0]
        dex_id = (p.get("dexId") or "").lower()
        slug = _SLUG_MAP.get(dex_id)
        quote = (p.get("quoteToken") or {}).get("address") or ""
        if slug:
            self._slug_cache[pair_address] = slug
        if quote:
            self._quote_cache[pair_address] = quote
        if not slug:
            logger.info(f"[DexScreener] unknown dexId={dex_id!r} for pair {pair_address[:12]} — add to _SLUG_MAP")
        return slug, quote or None

    async def _fetch_candles(
        self,
        pool_address: str,
        aggregate: int,
        limit: int,
        timeframe: str = "minute",
    ) -> List[Candle]:
        """Fetch OHLCV bars from DexScreener internal API.

        timeframe: "minute"  → res = aggregate (1, 5, 15)
                   "hour"    → res = aggregate * 60 (60 = 1h, 240 = 4h)
        """
        # Map (timeframe, aggregate) → DexScreener res
        if timeframe == "hour":
            res = aggregate * 60
        else:
            res = aggregate
        if res not in _RES_MAP:
            logger.debug(f"[DexScreener] unsupported res={res} (tf={timeframe} agg={aggregate})")
            return []

        key = f"{res}:{pool_address}:{limit}"
        now = time.monotonic()
        async with self._lock:
            cached = self._cache.get(key)
            if cached and (now - cached[0]) < self._cache_ttl:
                return cached[1]
            await self._throttle(now)

        slug, quote = await self._resolve_pool_meta(pool_address)
        if not slug or not quote:
            return []  # Caller falls back to GT

        url = (
            f"{_DEXS_BASE}/dex/chart/amm/v3/{slug}/bars/solana/{pool_address}"
            f"?res={_RES_MAP[res]}&cb={limit}&q={quote}"
        )
        try:
            sess = self._ensure_session()
            resp = await asyncio.to_thread(
                sess.get, url, timeout=10,
                headers={
                    "Origin": "https://dexscreener.com",
                    "Referer": "https://dexscreener.com/",
                    "Accept": "*/*",
                },
            )
            if resp.status_code != 200:
                logger.info(f"[DexScreener] {pool_address[:12]} res={res}: HTTP {resp.status_code}")
                return []
            raw = resp.content
        except Exception as e:
            logger.info(f"[DexScreener] fetch error {pool_address[:12]} res={res}: {e}")
            return []

        bars = parse_chart_bars(raw)
        if not bars:
            return []

        # Translate to Candle objects. open_time in SECONDS to match
        # GeckoTerminalClient. close_time = open_time + (res*60 - 1).
        candle_secs = res * 60
        candles: List[Candle] = []
        for b in bars:
            ts_s = b["ts_ms"] // 1000
            candles.append(Candle(
                open_time=ts_s,
                open=b["open"],
                high=b["high"],
                low=b["low"],
                close=b["close"],
                volume=b["volume_usd"],  # NOTE: USD volume; GT returns base-token vol
                close_time=ts_s + candle_secs - 1,
            ))
        candles.sort(key=lambda c: c.open_time)
        async with self._lock:
            self._cache[key] = (time.monotonic(), candles)
        return candles

    # GT-compatible methods (drop-in)
    async def fetch_1m(self, pool_address: str, limit: int = 5) -> List[Candle]:
        return await self._fetch_candles(pool_address, aggregate=1, limit=limit)

    async def fetch_5m(self, pool_address: str, limit: int = 100) -> List[Candle]:
        return await self._fetch_candles(pool_address, aggregate=5, limit=limit)

    async def fetch_15m(self, pool_address: str, limit: int = 96) -> List[Candle]:
        return await self._fetch_candles(pool_address, aggregate=15, limit=limit)

    async def fetch_1h(self, pool_address: str, limit: int = 48) -> List[Candle]:
        return await self._fetch_candles(
            pool_address, aggregate=1, limit=limit, timeframe="hour",
        )
