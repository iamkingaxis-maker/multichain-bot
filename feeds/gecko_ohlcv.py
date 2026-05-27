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
        # Request coalescing — when N parallel callers request the same key,
        # only one HTTP request goes out and the rest await its result.
        # Without this, all N race past the cache check (empty) and burst
        # GT simultaneously, triggering 429s even with self-imposed throttle.
        self._in_flight: Dict[str, "asyncio.Future"] = {}
        self._session_factory = session_factory or (
            lambda: aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        )

    async def fetch_5m(self, pool_address: str, limit: int = 100,
                       cache_ttl_override: Optional[int] = None) -> List[Candle]:
        return await self._fetch_candles(
            pool_address, aggregate=5, limit=limit,
            cache_ttl_override=cache_ttl_override,
        )

    async def fetch_1m(self, pool_address: str, limit: int = 5,
                       cache_ttl_override: Optional[int] = None) -> List[Candle]:
        """
        Fetch raw 1-minute candles for fine-grained entry confirmation.
        Default limit=5 (last 5 minutes) — enough to detect a recent
        green close while staying under the rate limit when called per-buy.

        cache_ttl_override is exposed so cycle-level features (e.g. SOL
        regime context) can use a longer cache than per-token candles —
        their staleness doesn't matter for regime use and the longer
        cache absorbs GT rate-limit pressure that was causing 80% of
        SOL fetches to come back empty (2026-05-12).
        """
        return await self._fetch_candles(
            pool_address, aggregate=1, limit=limit,
            cache_ttl_override=cache_ttl_override,
        )

    async def fetch_15m(self, pool_address: str, limit: int = 96) -> List[Candle]:
        """
        Fetch 15-minute candles. Default limit=96 (24h coverage) — used for
        anchored-VWAP at signal time. For tokens younger than 24h the API
        returns however many candles exist since launch.
        """
        return await self._fetch_candles(pool_address, aggregate=15, limit=limit)

    async def fetch_1h(self, pool_address: str, limit: int = 48) -> List[Candle]:
        """
        Fetch 1-hour candles. Default limit=48 (2-day coverage) — used for
        higher-timeframe trend alignment in chart_reader. Uses GT's hour
        endpoint with aggregate=1 (NOT minute with aggregate=60 — that
        returns empty).

        Uses 300s cache (vs 60s default for shorter TFs) — 1h candles only
        update once per hour, so the longer cache absorbs transient
        rate-limit blips without staleness.
        """
        return await self._fetch_candles(
            pool_address, aggregate=1, limit=limit, timeframe="hour",
            cache_ttl_override=300,
        )

    async def fetch_recent_trades(self, pool_address: str, limit: int = 30) -> List[dict]:
        """
        Fetch recent trades for a pool (most-recent first). Each trade dict
        has at minimum: kind ("buy"/"sell"), volume_in_usd, block_timestamp.

        Used at signal-fire time only (one call per buy, not per token).
        Cached 60s to coalesce duplicate signals on the same pool.
        """
        key = f"trades:{pool_address}:{limit}"
        now = time.monotonic()
        async with self._lock:
            cached = self._cache.get(key)
            if cached and (now - cached[0]) < self._cache_ttl:
                return cached[1]
            await self._throttle(now)

        url = (
            f"{_GT_BASE}/networks/solana/pools/{pool_address}/trades"
            f"?trade_volume_in_usd_greater_than=0"
        )
        try:
            async with self._session_factory() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.info(f"[GeckoOHLCV] trades {pool_address[:12]}: HTTP {resp.status}")
                        return []
                    data = await resp.json()
        except Exception as e:
            logger.info(f"[GeckoOHLCV] trades fetch error for {pool_address[:12]}: {e}")
            return []

        trades = []
        for item in (data.get("data") or [])[:limit]:
            attrs = item.get("attributes") or {}
            trades.append({
                "kind": attrs.get("kind") or "",  # "buy" or "sell"
                "volume_usd": float(attrs.get("volume_in_usd") or 0),
                "ts": attrs.get("block_timestamp") or "",
            })
        async with self._lock:
            self._cache[key] = (time.monotonic(), trades)
        return trades

    async def _fetch_candles(
        self, pool_address: str, aggregate: int, limit: int,
        timeframe: str = "minute",
        cache_ttl_override: Optional[int] = None,
    ) -> List[Candle]:
        # GT supports timeframes: minute (aggregate 1/5/15), hour
        # (aggregate 1/4/12), day (aggregate 1). Caller picks the
        # endpoint via `timeframe`. The 60-min "1h candle" path uses
        # timeframe=hour, aggregate=1 — NOT minute with aggregate=60
        # (that returns empty).
        ttl = cache_ttl_override if cache_ttl_override is not None else self._cache_ttl
        key = f"{timeframe}:{aggregate}:{pool_address}:{limit}"
        now = time.monotonic()
        # Request coalescing — if another task is already fetching this key,
        # await its result instead of starting a second HTTP call. Without
        # this, N parallel per-token evaluations all race past the cache
        # check at once and burst GT, triggering 429s.
        async with self._lock:
            cached = self._cache.get(key)
            if cached and (now - cached[0]) < ttl:
                return cached[1]
            existing_future = self._in_flight.get(key)
            if existing_future is not None:
                future = existing_future
                i_am_fetcher = False
            else:
                future = asyncio.get_event_loop().create_future()
                self._in_flight[key] = future
                await self._throttle(now)
                i_am_fetcher = True

        if not i_am_fetcher:
            try:
                return await future
            except Exception:
                return []

        url = (
            f"{_GT_BASE}/networks/solana/pools/{pool_address}/ohlcv/{timeframe}"
            f"?aggregate={aggregate}&limit={limit}&currency=usd"
        )
        candles: List[Candle] = []
        try:
            try:
                async with self._session_factory() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            logger.info(f"[GeckoOHLCV] {pool_address[:12]}: HTTP {resp.status}")
                        else:
                            data = await resp.json()
                            candles = self._parse(data)
            except Exception as e:
                logger.info(f"[GeckoOHLCV] fetch error for {pool_address[:12]}: {e}")
        finally:
            # Always release the in-flight slot and unblock waiters, even
            # if cancelled or raised. Without finally, CancelledError leaves
            # the future unset and other tasks hang indefinitely.
            async with self._lock:
                if candles:
                    self._cache[key] = (time.monotonic(), candles)
                self._in_flight.pop(key, None)
                if not future.done():
                    future.set_result(candles)
        return candles

    async def fetch_trending_pools(self, pages: int = 3) -> List[dict]:
        """
        Fetch trending Solana pools from GeckoTerminal. Returns
        DexScreener-style pair dicts for direct merging with other sources.
        pages=1 → 20 pools, pages=3 → up to 60 pools.
        """
        out: List[dict] = []
        for page in range(1, pages + 1):
            now = time.monotonic()
            async with self._lock:
                await self._throttle(now)
            url = (
                f"{_GT_BASE}/networks/solana/trending_pools"
                f"?page={page}&include=base_token"
            )
            try:
                async with self._session_factory() as session:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status != 200:
                            logger.debug(f"[GeckoOHLCV] trending page {page} HTTP {resp.status}")
                            break
                        data = await resp.json()
            except Exception as e:
                logger.debug(f"[GeckoOHLCV] trending page {page} err: {e}")
                break
            out.extend(self._parse_trending(data))
            if len(data.get("data") or []) < 20:
                break  # reached last page
        return out

    async def fetch_pool_feed(self, path: str, pages: int = 2,
                              min_liq_usd: float = 0.0) -> List[dict]:
        """Fetch any GT Solana pool feed (e.g. 'new_pools' or
        'pools?sort=h24_volume_usd_desc') as DexScreener-style pair dicts.

        Same response schema as trending_pools, so it reuses _parse_trending.
        `min_liq_usd` bounds the result to pools with reserve >= that — this
        caps downstream DS enrichment + per-candidate scan cost (the fresh-pool
        feeds otherwise return thousands of dust pools). Added 2026-05-27 to
        close the discovery coverage gap (we polled trending only → missed
        ~half the liquid fresh movers; see reference_universe_coverage_gap).
        """
        out: List[dict] = []
        sep = "&" if "?" in path else "?"
        for page in range(1, pages + 1):
            now = time.monotonic()
            async with self._lock:
                await self._throttle(now)
            url = f"{_GT_BASE}/networks/solana/{path}{sep}page={page}&include=base_token"
            try:
                async with self._session_factory() as session:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status != 200:
                            logger.debug(f"[GeckoOHLCV] {path} page {page} HTTP {resp.status}")
                            break
                        data = await resp.json()
            except Exception as e:
                logger.debug(f"[GeckoOHLCV] {path} page {page} err: {e}")
                break
            parsed = self._parse_trending(data)
            if min_liq_usd > 0:
                parsed = [p for p in parsed
                          if (p.get("liquidity") or {}).get("usd", 0) >= min_liq_usd]
            out.extend(parsed)
            if len(data.get("data") or []) < 20:
                break
        return out

    async def fetch_new_pools(self, pages: int = 2, min_liq_usd: float = 0.0) -> List[dict]:
        """Freshest Solana pools — where movers appear BEFORE they hit trending."""
        return await self.fetch_pool_feed("new_pools", pages, min_liq_usd)

    async def fetch_top_volume_pools(self, pages: int = 2, min_liq_usd: float = 0.0) -> List[dict]:
        """Highest-h24-volume pools — catches movers regardless of name (kills the
        meme-keyword search bias)."""
        return await self.fetch_pool_feed("pools?sort=h24_volume_usd_desc", pages, min_liq_usd)

    @staticmethod
    def _parse_trending(data: dict) -> List[dict]:
        items = data.get("data") or []
        included = {i["id"]: i for i in (data.get("included") or [])}
        out: List[dict] = []
        for item in items:
            try:
                attrs = item.get("attributes") or {}
                rels = item.get("relationships") or {}
                base_ref = (rels.get("base_token") or {}).get("data") or {}
                base_id = base_ref.get("id") or ""
                base_addr = ""
                base_sym = "?"
                base_info = included.get(base_id) or {}
                bi_attrs = base_info.get("attributes") or {}
                base_addr = bi_attrs.get("address") or (
                    base_id.split("_", 1)[1] if "_" in base_id else ""
                )
                base_sym = bi_attrs.get("symbol") or attrs.get("name", "?").split(" /")[0]

                if not base_addr or base_addr.startswith("0x"):
                    continue

                vol = attrs.get("volume_usd") or {}
                pc = attrs.get("price_change_percentage") or {}
                reserve = float(attrs.get("reserve_in_usd") or 0)
                mcap = float(
                    attrs.get("market_cap_usd")
                    or attrs.get("fdv_usd")
                    or 0
                )
                created_iso = attrs.get("pool_created_at") or ""
                created_ms = 0
                if created_iso:
                    try:
                        import datetime as _dt
                        dt = _dt.datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
                        created_ms = int(dt.timestamp() * 1000)
                    except Exception:
                        pass

                out.append({
                    "chainId": "solana",
                    "baseToken": {"address": base_addr, "symbol": base_sym},
                    "pairAddress": attrs.get("address") or "",
                    "priceUsd": str(attrs.get("base_token_price_usd") or 0),
                    "marketCap": mcap,
                    "liquidity": {"usd": reserve},
                    "priceChange": {
                        "m5": float(pc.get("m5") or 0),
                        "h1": float(pc.get("h1") or 0),
                        "h6": float(pc.get("h6") or 0),
                        "h24": float(pc.get("h24") or 0),
                    },
                    "volume": {
                        "m5": float(vol.get("m5") or 0),
                        "h1": float(vol.get("h1") or 0),
                        "h6": float(vol.get("h6") or 0),
                        "h24": float(vol.get("h24") or 0),
                    },
                    "pairCreatedAt": created_ms,
                    "_source": "geckoterminal",
                })
            except Exception:
                continue
        return out

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
