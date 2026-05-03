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
) -> ChartData:
    """Fetch all four timeframes for a pool in parallel.

    Returns a ChartData with whatever fetches succeeded. Never raises —
    individual fetch failures are caught by the GT client and surface
    as empty lists.

    Defaults give 1h / 12h / 24h / 2d of coverage on the four
    timeframes — enough history for swing-pivot detection (S/R) and
    trend evaluation across all timeframes.
    """
    if not pool_address:
        return ChartData(pool_address="")

    # Sequential fetch (not asyncio.gather) — GT enforces a per-second burst
    # ceiling on top of its per-minute quota, and parallel-gather all-four
    # routinely returned 4-empty even when our local 30/min budget had
    # headroom. Audit on 14 post-refactor trades: 5/14 had full coverage,
    # 9/14 had ZERO coverage — perfectly all-or-nothing because the gather
    # bursted past GT's per-second cap and every call returned empty.
    # Serializing lets the GT client's _throttle properly space requests
    # and the 60s cache absorbs the small latency cost (~500ms gather ->
    # ~2s sequential is acceptable inside a scan cycle).
    async def _safe_fetch(coro):
        try:
            r = await coro
            return r or []
        except Exception:
            return []

    candles_1m = await _safe_fetch(gt_client.fetch_1m(pool_address, limit=limit_1m))
    candles_5m = await _safe_fetch(gt_client.fetch_5m(pool_address, limit=limit_5m))
    candles_15m = await _safe_fetch(gt_client.fetch_15m(pool_address, limit=limit_15m))
    candles_1h = await _safe_fetch(gt_client.fetch_1h(pool_address, limit=limit_1h))

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
