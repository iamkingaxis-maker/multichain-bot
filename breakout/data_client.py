"""
BinanceUSClient — public REST endpoints (api.binance.us).

No auth. Rate-limit note: we sit well under 1200 WEIGHT/min budget.
Errors: raise for HTTP 4xx; callers can catch and count.
"""

import logging
from typing import Optional

import aiohttp

from breakout.scoring import Kline

logger = logging.getLogger(__name__)

_BASE = "https://api.binance.us/api/v3"


def parse_klines(raw: list) -> list[Kline]:
    """Binance klines array → list[Kline]. Fields are strings in REST."""
    out = []
    for row in raw:
        out.append(Kline(
            open_time=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            close_time=int(row[6]),
        ))
    return out


class BinanceUSClient:
    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def fetch_24h_tickers(self) -> list[dict]:
        """GET /ticker/24hr (no symbol → all symbols)."""
        url = f"{_BASE}/ticker/24hr"
        async with (await self._get_session()).get(url) as r:
            r.raise_for_status()
            return await r.json()

    async def fetch_klines(
        self, symbol: str, interval: str = "15m", limit: int = 100
    ) -> list[Kline]:
        url = f"{_BASE}/klines?symbol={symbol}&interval={interval}&limit={limit}"
        async with (await self._get_session()).get(url) as r:
            r.raise_for_status()
            raw = await r.json()
        return parse_klines(raw)

    async def fetch_order_book(self, symbol: str, depth: int = 10) -> dict:
        url = f"{_BASE}/depth?symbol={symbol}&limit={depth}"
        async with (await self._get_session()).get(url) as r:
            r.raise_for_status()
            return await r.json()
