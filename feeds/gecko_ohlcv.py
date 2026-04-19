"""
GeckoTerminal OHLCV client — free, no API key, 30 req/min limit.
Endpoint: GET /api/v2/networks/solana/pools/{pool}/ohlcv/minute?aggregate=5&limit=100
Returns 5m candles oldest-first. In-memory 60s cache to stay under rate limit.
"""
import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional, Tuple

import aiohttp

from feeds.candle_utils import Candle

logger = logging.getLogger(__name__)

_GT_BASE = "https://api.geckoterminal.com/api/v2"


class GeckoTerminalClient:
    def __init__(
        self,
        session_factory: Optional[Callable[[], object]] = None,
        cache_ttl: int = 60,
        rate_per_min: int = 25,
    ):
        self._cache_ttl = cache_ttl
        self._cache: Dict[str, Tuple[float, List[Candle]]] = {}
        self._rate_per_min = rate_per_min
        self._request_log: List[float] = []
        self._lock = asyncio.Lock()
        self._session_factory = session_factory or (
            lambda: aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        )

    async def fetch_5m(self, pool_address: str, limit: int = 100) -> List[Candle]:
        key = f"5m:{pool_address}:{limit}"
        now = time.monotonic()
        async with self._lock:
            cached = self._cache.get(key)
            if cached and (now - cached[0]) < self._cache_ttl:
                return cached[1]
            await self._throttle(now)

        url = (
            f"{_GT_BASE}/networks/solana/pools/{pool_address}/ohlcv/minute"
            f"?aggregate=5&limit={limit}&currency=usd"
        )
        try:
            async with self._session_factory() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.debug(f"[GeckoOHLCV] {pool_address}: HTTP {resp.status}")
                        return []
                    data = await resp.json()
        except Exception as e:
            logger.debug(f"[GeckoOHLCV] fetch error for {pool_address}: {e}")
            return []

        candles = self._parse(data)
        async with self._lock:
            self._cache[key] = (time.monotonic(), candles)
        return candles

    async def _throttle(self, now: float):
        cutoff = now - 60.0
        self._request_log = [t for t in self._request_log if t > cutoff]
        if len(self._request_log) >= self._rate_per_min:
            sleep_s = 60.0 - (now - self._request_log[0]) + 0.5
            logger.debug(f"[GeckoOHLCV] rate-limit sleep {sleep_s:.2f}s")
            await asyncio.sleep(max(0.0, sleep_s))
        self._request_log.append(time.monotonic())

    @staticmethod
    def _parse(data: dict) -> List[Candle]:
        try:
            rows = data["data"]["attributes"]["ohlcv_list"]
        except (KeyError, TypeError):
            return []
        out: List[Candle] = []
        for row in rows:
            try:
                ts, o, h, lo, c, v = row
                out.append(Candle(
                    open_time=int(ts),
                    open=float(o),
                    high=float(h),
                    low=float(lo),
                    close=float(c),
                    volume=float(v),
                    close_time=int(ts) + 299,
                ))
            except (ValueError, TypeError):
                continue
        out.sort(key=lambda k: k.open_time)
        return out
