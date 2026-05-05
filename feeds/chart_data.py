"""
Multi-timeframe chart data assembly.

Phase 0 of the chart-reading rebuild. Single source that fetches and
caches the four candle timeframes used by all chart-reading features
downstream:

  1m  → 60 candles  (1h window)  — candlestick pattern detection
  5m  → 144 candles (12h window) — 5m trend, 5m S/R pivots, recent action
  15m → 96 candles  (24h window) — 15m trend, 15m S/R pivots, VWAP
  1h  → 48 candles  (2d window)  — higher-timeframe trend alignment

The dataclass `ChartData` is passed once per signal-fire to the chart
reader. Subsequent feature computations (candle patterns, MTF trend,
S/R, volume profile, chart patterns) all consume the same `ChartData`
instance — no duplicate fetches.

Fetches happen in parallel via asyncio.gather — total wall-clock cost
is roughly equal to the slowest single fetch (~300-500ms). The
GeckoTerminalClient has a built-in 60s cache so repeated assembly on
the same pool within a minute returns instantly.

Rate-limit math: 4 GT calls per signal. Free tier allows 25 req/min.
With dedup cache and typical scan rate of ~1 deep-filter candidate per
30s, we stay well under. If multiple deep candidates land in the same
cycle, the rate limiter inside the GT client handles backoff.

Fail-open philosophy: if any single timeframe fetch fails or returns
empty, ChartData still constructs with an empty list for that
timeframe. Downstream features check `len(candles) > 0` before
computing — missing data → feature absent → fail-closed at the
chart_reader level (not here).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from feeds.candle_utils import Candle
from feeds.gecko_ohlcv import GeckoTerminalClient

try:
    from feeds.dexscreener_client import DexScreenerClient
except Exception:  # noqa: BLE001 — keep optional; fall back to GT only if missing
    DexScreenerClient = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


@dataclass
class ChartData:
    """Multi-timeframe candle bundle for a single pool at a single moment.

    All lists are sorted oldest-first (so cs[-1] is the most recent
    candle, cs[0] is the oldest in the window). Empty list = fetch
    failed or token too young for that timeframe.
    """
    pool_address: str
    candles_1m: List[Candle] = field(default_factory=list)
    candles_5m: List[Candle] = field(default_factory=list)
    candles_15m: List[Candle] = field(default_factory=list)
    candles_1h: List[Candle] = field(default_factory=list)

    def has_full_coverage(self) -> bool:
        """True only if all four timeframes returned non-empty.
        Used by chart_reader to decide fail-closed for full-context
        features (S/R, MTF). Single-timeframe features (e.g. last 5m
        candle pattern) only need their own timeframe present."""
        return (
            len(self.candles_1m) > 0
            and len(self.candles_5m) > 0
            and len(self.candles_15m) > 0
            and len(self.candles_1h) > 0
        )

    def coverage_summary(self) -> str:
        return (
            f"1m={len(self.candles_1m)} "
            f"5m={len(self.candles_5m)} "
            f"15m={len(self.candles_15m)} "
            f"1h={len(self.candles_1h)}"
        )


async def assemble_chart_data(
    gt_client: GeckoTerminalClient,
    pool_address: str,
    *,
    limit_1m: int = 60,
    limit_5m: int = 144,
    limit_15m: int = 96,
    limit_1h: int = 48,
    dexs_client: "DexScreenerClient | None" = None,
) -> ChartData:
    """Fetch all four timeframes for a pool.

    If `dexs_client` is provided, DexScreener's internal binary chart API is
    used as the PRIMARY source — much higher rate-limit headroom than GT's
    free tier (which was bottlenecking coverage at 6-20%). On any per-
    timeframe miss (empty result), we fall back to GT for that specific
    timeframe so partial DexScreener outages still produce a usable bundle.

    If `dexs_client` is None, we use GT only (legacy behaviour).

    Returns a ChartData with whatever fetches succeeded. Never raises —
    individual fetch failures are caught by the underlying client and
    surface as empty lists.

    Defaults give 1h / 12h / 24h / 2d of coverage on the four
    timeframes — enough history for swing-pivot detection (S/R) and
    trend evaluation across all timeframes.
    """
    if not pool_address:
        return ChartData(pool_address="")

    async def _safe(coro):
        try:
            r = await coro
            return r or []
        except Exception:
            return []

    # Sequential fetch — both GT and DexScreener enforce per-second burst
    # ceilings on top of their per-minute quotas; parallel asyncio.gather
    # routinely returned 4-empty even with budget headroom. Sequential
    # fetches let the rate-limiter inside each client space requests
    # properly. The 60s cache absorbs the latency cost on re-scans.

    async def _fetch_one(timeframe: str, gt_factory, dexs_factory):
        """Try DexScreener first if available; fall back to GT on empty.

        Factories (no-arg callables returning a coroutine) are used so we
        only construct the coroutine for the path we actually await — avoids
        orphan-coroutine RuntimeWarnings.

        Retries the full DS→GT flow once after a 500ms backoff if both
        sources return empty on the first attempt. Catches transient 429s,
        timeouts, and slug-resolution misses that resolve on a second hit.
        Empirical: 86% of `?` 1h verdicts are tokens that SHOULD have data
        (age >= 6h, established pools) — fetch failures, not history gaps.
        """
        for attempt in range(2):
            if dexs_client is not None and dexs_factory is not None:
                r = await _safe(dexs_factory())
                if r:
                    return r
            r = await _safe(gt_factory())
            if r or attempt == 1:
                return r
            await asyncio.sleep(0.5)
        return []

    candles_1m = await _fetch_one(
        "1m",
        lambda: gt_client.fetch_1m(pool_address, limit=limit_1m),
        (lambda: dexs_client.fetch_1m(pool_address, limit=limit_1m)) if dexs_client else None,
    )
    candles_5m = await _fetch_one(
        "5m",
        lambda: gt_client.fetch_5m(pool_address, limit=limit_5m),
        (lambda: dexs_client.fetch_5m(pool_address, limit=limit_5m)) if dexs_client else None,
    )
    candles_15m = await _fetch_one(
        "15m",
        lambda: gt_client.fetch_15m(pool_address, limit=limit_15m),
        (lambda: dexs_client.fetch_15m(pool_address, limit=limit_15m)) if dexs_client else None,
    )
    candles_1h = await _fetch_one(
        "1h",
        lambda: gt_client.fetch_1h(pool_address, limit=limit_1h),
        (lambda: dexs_client.fetch_1h(pool_address, limit=limit_1h)) if dexs_client else None,
    )

    cd = ChartData(
        pool_address=pool_address,
        candles_1m=candles_1m,
        candles_5m=candles_5m,
        candles_15m=candles_15m,
        candles_1h=candles_1h,
    )

    if not cd.has_full_coverage():
        logger.debug(
            f"[ChartData] {pool_address[:12]} partial coverage: "
            f"{cd.coverage_summary()}"
        )

    return cd
