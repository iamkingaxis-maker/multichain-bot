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
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from feeds.candle_utils import Candle
from feeds.dexscreener_chart_format import parse_chart_bars
from feeds.dexscreener_trades_format import parse_trades

logger = logging.getLogger(__name__)

_DEXS_BASE = "https://io.dexscreener.com"

# ── slug/quote cache persistence (BUILD A, 2026-06-17) ────────────────────────
# _slug_cache and _quote_cache are stable pool->slug/quote IDENTITY mappings
# (not prices) — they rarely change for a given pool. In-memory only, they were
# wiped on every restart, forcing a cold cycle to re-resolve ~190 tokens via the
# UNTHROTTLED public meta call (api.dexscreener.com/.../pairs/solana/{pair}).
# Persisting them to DATA_DIR kills that cold-after-deploy re-resolution.
# Default ON: pure speed/cost win with no signal effect (the live FILL re-prices
# via BUY_REPRICE; this only affects pool->slug identity resolution).
_SLUG_CACHE_PERSIST = (
    os.environ.get("SLUG_CACHE_PERSIST", "on").strip().lower()
    not in ("off", "0", "false", "no")
)
_DATA_DIR = os.environ.get("DATA_DIR", ".")
_SLUG_CACHE_PATH = os.path.join(_DATA_DIR, "dexs_slug_quote_cache.json")
# Throttle disk writes: persist at most once per this many seconds.
_SLUG_CACHE_SAVE_INTERVAL = 60.0

# Shared singleton accessor (2026-06-12 audit A1/A2): other modules fetching
# io.dexscreener via curl_cffi must use THIS client's private executor +
# circuit breaker, never bare asyncio.to_thread (which saturates the global
# ~32-thread pool the dashboard depends on — the 06-11 20:00 incident).
_SHARED: "DexScreenerClient | None" = None


def shared_client() -> "DexScreenerClient":
    global _SHARED
    if _SHARED is None:
        _SHARED = DexScreenerClient()
    return _SHARED


async def run_ds_fetch(fn, *args, **kwargs):
    """Run a sync DS-bound callable on the private DS executor, honoring the
    circuit breaker. Returns None when the circuit is open."""
    cl = shared_client()
    if not cl._circuit_ok():
        return None
    try:
        out = await cl._run_fetch(fn, *args, **kwargs)
        cl._record_result(True)
        return out
    except Exception:
        cl._record_result(False)
        raise
_DEXS_PUBLIC = "https://api.dexscreener.com/latest/dex"

# DexScreener public dexId → io.dexscreener internal slug mapping.
# Discovered via network inspection on dexscreener.com pair pages.
# Add new mappings as the bot encounters new DEX types.
_SLUG_MAP: Dict[str, str] = {
    "raydium": "solamm",
    "pumpswap": "pumpfundex",
    "pumpfun": "pumpfundex",  # alternate naming on some pairs
    "meteora": "meteora",      # validated 2026-05-05 — dexId == slug for meteora pools
    # TODO when encountered: orca, openbookv2, etc.
}

# When a dexId isn't in _SLUG_MAP, try the dexId itself as the slug. For
# many Solana DEXes the dexId IS the slug (meteora is the canonical case).
# We track which dynamic slugs end up returning 200 vs 4xx and only retry
# the success ones — avoids burning calls on permanently-bad dexIds.
_DYNAMIC_SLUG_CACHE: Dict[str, bool] = {}  # dexId → True (works), False (doesn't)

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
        # BUILD A: persisted slug/quote identity cache (survives restarts).
        self._persist_path = _SLUG_CACHE_PATH
        self._persist_enabled = _SLUG_CACHE_PERSIST
        self._last_persist_save = 0.0
        self._persist_dirty = False
        if self._persist_enabled:
            self._load_slug_cache()
        self._rate_per_min = rate_per_min
        self._request_log: List[float] = []
        self._lock = asyncio.Lock()
        self._session = None  # lazy-init curl_cffi session

        # BUILD B (2026-06-17) — chart-BAR cross-cycle cache reuse.
        # The bar-fetch cache TTL (used ONLY for OHLCV chart bars, NOT for the
        # trades cache, which keeps `cache_ttl`) is env-tunable via
        # CHART_BAR_TTL_SECS. Default = the constructor cache_ttl (60s) so
        # behaviour is byte-identical when the env is unset. Setting it ABOVE
        # the scan-cycle length (e.g. 180s) lets sticky tokens (re-scanned every
        # cycle) reuse bars across cycles instead of re-fetching every cycle.
        # The scan DECISION tolerates minutes-old bars; the live FILL re-prices
        # separately via the BUY_REPRICE guard, so staleness here is safe.
        # Fail-safe: any bad/zero value falls back to the constructor cache_ttl.
        self._bar_cache_ttl = cache_ttl
        try:
            _bar_ttl = int(os.environ.get("CHART_BAR_TTL_SECS", "").strip())
            if _bar_ttl > 0:
                self._bar_cache_ttl = _bar_ttl
        except (TypeError, ValueError):
            pass

        # BUILD B (2026-06-17) — DS fetch executor throughput.
        # Dedicated bounded executor (2026-06-11): when io.dexscreener rate-
        # limits us, each call hangs its thread for the full timeout. On the
        # GLOBAL to_thread pool (~32 threads) that starved the dashboard's
        # serialization threads -> every endpoint went dark while the loop
        # crawled. A private bounded pool caps the blast radius to DS itself.
        # Worker count is env-tunable via DS_FETCH_WORKERS (default 4, the prior
        # hard-coded value; clamped to [1, 12] to keep the blast radius bounded).
        _workers = 4
        try:
            _w = int(os.environ.get("DS_FETCH_WORKERS", "").strip())
            if _w > 0:
                _workers = max(1, min(12, _w))
        except (TypeError, ValueError):
            pass
        self._fetch_workers = _workers

        # DS per-call HTTP timeout (seconds), env-tunable via DS_FETCH_TIMEOUT_SECS.
        # Default = 5 (the prior hard-coded value). Bad/zero values fall back.
        _timeout = 5
        try:
            _t = int(os.environ.get("DS_FETCH_TIMEOUT_SECS", "").strip())
            if _t > 0:
                _timeout = _t
        except (TypeError, ValueError):
            pass
        self._fetch_timeout = _timeout

        from concurrent.futures import ThreadPoolExecutor
        self._executor = ThreadPoolExecutor(
            max_workers=self._fetch_workers, thread_name_prefix="dexs")
        # Circuit breaker: consecutive failures open the circuit; while open,
        # calls return empty immediately (callers fall back to GeckoTerminal).
        self._fail_streak = 0
        self._circuit_open_until = 0.0

    # ── slug/quote cache persistence (BUILD A) ───────────────────────────────
    def _load_slug_cache(self) -> None:
        """Load persisted slug/quote identity mappings on init. Best-effort;
        a missing/corrupt file just leaves the in-memory caches empty (cold)."""
        try:
            with open(self._persist_path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:
            logger.warning(f"[DexScreener] slug-cache load failed: {e}")
            return
        slugs = data.get("slug") if isinstance(data, dict) else None
        quotes = data.get("quote") if isinstance(data, dict) else None
        if isinstance(slugs, dict):
            self._slug_cache.update({str(k): str(v) for k, v in slugs.items() if v})
        if isinstance(quotes, dict):
            self._quote_cache.update({str(k): str(v) for k, v in quotes.items() if v})
        logger.info(
            f"[DexScreener] slug-cache loaded: {len(self._slug_cache)} slug / "
            f"{len(self._quote_cache)} quote mappings (cold re-resolution avoided)"
        )

    def _save_slug_cache(self, force: bool = False) -> None:
        """Atomically persist slug/quote caches to DATA_DIR. Throttled to once
        per _SLUG_CACHE_SAVE_INTERVAL unless force=True. No-ops when persistence
        is disabled or nothing changed since the last save."""
        if not self._persist_enabled or not self._persist_dirty:
            return
        now = time.monotonic()
        if not force and (now - self._last_persist_save) < _SLUG_CACHE_SAVE_INTERVAL:
            return
        try:
            tmp = self._persist_path + ".tmp"
            payload = {"slug": dict(self._slug_cache), "quote": dict(self._quote_cache)}
            d = os.path.dirname(self._persist_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(tmp, "w") as f:
                json.dump(payload, f)
            os.replace(tmp, self._persist_path)
            self._last_persist_save = now
            self._persist_dirty = False
        except Exception as e:
            logger.warning(f"[DexScreener] slug-cache save failed: {e}")

    async def _run_fetch(self, fn, *args, **kwargs):
        """Run a sync curl_cffi call on the private executor."""
        loop = asyncio.get_running_loop()
        import functools
        return await loop.run_in_executor(
            self._executor, functools.partial(fn, *args, **kwargs))

    def _circuit_ok(self) -> bool:
        return time.monotonic() >= self._circuit_open_until

    def _record_result(self, ok: bool):
        if ok:
            self._fail_streak = 0
            return
        self._fail_streak += 1
        if self._fail_streak >= 5:
            self._circuit_open_until = time.monotonic() + 300
            self._fail_streak = 0
            logger.warning("[DexScreener] circuit OPEN 5min — endpoint degraded "
                           "(5 consecutive failures); falling back to GT")

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
        # 2026-06-12 audit A3: sleep OUTSIDE any caller-held context where
        # possible — compute first, sleep after appending intent. The sleep
        # previously ran while _lock was held by the caller, serializing every
        # unrelated pool behind one token's rate-limit wait.
        cutoff = now - 60.0
        self._request_log = [t for t in self._request_log if t > cutoff]
        sleep_s = 0.0
        if len(self._request_log) >= self._rate_per_min:
            sleep_s = max(0.0, 60.0 - (now - self._request_log[0]) + 0.5)
            logger.debug(f"[DexScreener] rate-limit sleep {sleep_s:.2f}s")
        self._request_log.append(time.monotonic())
        if sleep_s:
            await asyncio.sleep(sleep_s)

    async def _resolve_pool_meta(self, pair_address: str) -> Tuple[Optional[str], Optional[str]]:
        """Resolve (dex_slug, quote_token_mint) for a pair. Cached per pool."""
        cached_slug = self._slug_cache.get(pair_address)
        cached_q = self._quote_cache.get(pair_address)
        if cached_slug and cached_q:
            return cached_slug, cached_q

        if not self._circuit_ok():
            return None, None
        url = f"{_DEXS_PUBLIC}/pairs/solana/{pair_address}"
        try:
            sess = self._ensure_session()
            resp = await self._run_fetch(sess.get, url, timeout=5)
            if resp.status_code != 200:
                logger.debug(f"[DexScreener] meta {pair_address[:12]}: HTTP {resp.status_code}")
                return None, None
            data = resp.json()
            self._record_result(True)
        except Exception as e:
            self._record_result(False)
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
        # Dynamic fallback: if dexId isn't in the static map, try the dexId
        # itself as a slug. Many Solana DEXes use dexId==slug. We cache the
        # outcome (True = works, False = doesn't) per dexId so we don't
        # retry permanently-bad ones. Validated by the first non-cached call.
        if not slug and dex_id:
            cache_hit = _DYNAMIC_SLUG_CACHE.get(dex_id)
            if cache_hit is True:
                slug = dex_id
            elif cache_hit is None:
                # First time seeing this dexId — let downstream try it as
                # the slug; the fetch will record success/failure into the
                # dynamic cache via _record_dynamic_slug_result.
                slug = dex_id
                logger.info(
                    f"[DexScreener] unknown dexId={dex_id!r} for pair {pair_address[:12]} "
                    f"— attempting dexId-as-slug fallback"
                )
        if slug:
            if self._slug_cache.get(pair_address) != slug:
                self._slug_cache[pair_address] = slug
                self._persist_dirty = True
        if quote:
            if self._quote_cache.get(pair_address) != quote:
                self._quote_cache[pair_address] = quote
                self._persist_dirty = True
        # BUILD A: persist newly-resolved identity mappings (throttled + atomic).
        if self._persist_dirty:
            self._save_slug_cache()
        return slug, quote or None

    @staticmethod
    def _record_dynamic_slug_result(dex_id: str, success: bool) -> None:
        prior = _DYNAMIC_SLUG_CACHE.get(dex_id)
        if prior is None:
            _DYNAMIC_SLUG_CACHE[dex_id] = success
            if success:
                logger.info(f"[DexScreener] dynamic slug {dex_id!r} VALIDATED — caching")
            else:
                logger.info(f"[DexScreener] dynamic slug {dex_id!r} INVALID — adding to denylist")

    async def _fetch_candles(
        self,
        pool_address: str,
        aggregate: int,
        limit: int,
        timeframe: str = "minute",
        cache_ttl_override: Optional[int] = None,
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

        # BUILD B: chart-bar cache TTL = env-tunable _bar_cache_ttl (default 60s,
        # the prior self._cache_ttl). When a per-call override is given (fetch_1h
        # passes 300s because 1h bars only change hourly), keep the LONGER of the
        # two so the env can only LENGTHEN reuse, never shorten the 1h floor.
        ttl = self._bar_cache_ttl
        if cache_ttl_override is not None:
            ttl = max(ttl, cache_ttl_override)
        key = f"{res}:{pool_address}:{limit}"
        now = time.monotonic()
        async with self._lock:
            cached = self._cache.get(key)
            if cached and (now - cached[0]) < ttl:
                return cached[1]
            await self._throttle(now)

        slug, quote = await self._resolve_pool_meta(pool_address)
        if not slug or not quote:
            return []  # Caller falls back to GT

        # Track whether this is a dynamic-slug attempt (slug not in static map)
        # so we can record success/failure for future calls.
        is_dynamic_slug = slug not in _SLUG_MAP.values()

        if not self._circuit_ok():
            return []  # circuit open — caller falls back to GT
        url = (
            f"{_DEXS_BASE}/dex/chart/amm/v3/{slug}/bars/solana/{pool_address}"
            f"?res={_RES_MAP[res]}&cb={limit}&q={quote}"
        )
        try:
            sess = self._ensure_session()
            resp = await self._run_fetch(
                sess.get, url, timeout=self._fetch_timeout,
                headers={
                    "Origin": "https://dexscreener.com",
                    "Referer": "https://dexscreener.com/",
                    "Accept": "*/*",
                },
            )
            if resp.status_code != 200:
                logger.info(f"[DexScreener] {pool_address[:12]} slug={slug} res={res}: HTTP {resp.status_code}")
                if is_dynamic_slug and resp.status_code in (400, 404):
                    self._record_dynamic_slug_result(slug, False)
                return []
            raw = resp.content
            self._record_result(True)
            if is_dynamic_slug:
                self._record_dynamic_slug_result(slug, True)
        except Exception as e:
            self._record_result(False)
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
        # 300s cache (vs 60s default for shorter TFs) — 1h candles only
        # update once per hour, so the longer cache absorbs transient
        # rate-limit blips and slug-resolution failures without staleness.
        return await self._fetch_candles(
            pool_address, aggregate=1, limit=limit, timeframe="hour",
            cache_ttl_override=300,
        )

    async def fetch_recent_trades(self, pool_address: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Fetch recent trades for a pool. Drop-in for
        GeckoTerminalClient.fetch_recent_trades — same return shape:
        list of {"kind": "buy"|"sell", "volume_usd": float, "ts": iso}.

        DexScreener returns up to ~100 trades per response (the `c=1`
        param appears to control batch size). We slice to `limit`.
        """
        key = f"trades:{pool_address}:{limit}"
        now = time.monotonic()
        async with self._lock:
            cached = self._cache.get(key)
            if cached and (now - cached[0]) < self._cache_ttl:
                return cached[1]  # type: ignore[return-value]
        # throttle OUTSIDE the lock (2026-06-12 audit A3): the rate-limit
        # sleep ran lock-held, serializing every unrelated pool behind one
        # token's wait
        await self._throttle(now)

        slug, quote = await self._resolve_pool_meta(pool_address)
        if not slug or not quote:
            return []

        if not self._circuit_ok():
            return []  # circuit open — caller falls back to GT
        url = (
            f"{_DEXS_BASE}/dex/log/amm/v4/{slug}/all/solana/{pool_address}"
            f"?q={quote}&c=1"
        )
        # Retry ONCE on a transient timeout/connection error (2026-07-01). The io
        # endpoint intermittently times out at the 5s budget -> [] -> GT fallback
        # which STRIPS maker -> unique_buyers_n=0 -> fleet-wide rug-gate. A single
        # retry recovers most transient misses AND — because a failure only counts
        # once — keeps the 5-consecutive-fail circuit from tripping and darking the
        # whole fleet for 5min. HTTP-status errors (slug/404/429) are NOT retried
        # (retry can't fix them). Maker fetch is off the critical fire path.
        raw = None
        for _attempt in range(2):
            try:
                sess = self._ensure_session()
                resp = await self._run_fetch(
                    sess.get, url, timeout=self._fetch_timeout,
                    headers={
                        "Origin": "https://dexscreener.com",
                        "Referer": "https://dexscreener.com/",
                        "Accept": "*/*",
                    },
                )
                if resp.status_code != 200:
                    logger.info(f"[DexScreener] trades {pool_address[:12]}: HTTP {resp.status_code}")
                    return []
                raw = resp.content
                self._record_result(True)
                break
            except Exception as e:
                if _attempt == 0:
                    await asyncio.sleep(0.3)  # brief backoff, then one retry
                    continue
                self._record_result(False)  # count the failure ONLY after the retry
                logger.info(f"[DexScreener] trades fetch error {pool_address[:12]} (after 1 retry): {e}")
                return []
        if raw is None:
            return []

        trades = parse_trades(raw)[:limit]
        async with self._lock:
            self._cache[key] = (time.monotonic(), trades)  # type: ignore[assignment]
        return trades
