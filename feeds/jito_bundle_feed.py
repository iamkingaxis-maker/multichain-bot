"""Jito MEV bundle activity feed.

Lightweight feed that polls Jito public stats endpoints to track MEV
activity as a macro-context feature. High Jito tip activity = aggressive
sniper bots competing for slots = caution flag for fresh entries.

Exposes two scalar gauges that callers can stamp into entry_meta:
  - jito_tip_floor_lamports   (current price floor for landing a bundle)
  - jito_tip_p99_lamports     (99th-percentile recent tip — sniper aggression)

Polled every 30s by default with a 60s in-memory cache. Fail-open: any
network or parse failure returns the last-known values or None.

Endpoints (Jito public; subject to change):
  GET https://bundles.jito.wtf/api/v1/bundles/tip_floor
      → [{"time": "...", "landed_tips_25th_percentile": <SOL>, ...}]

This module is intentionally minimal. It does NOT touch the trading
path. To wire into dip_scanner, call ``JitoBundleFeed.snapshot()`` and
fold the returned dict into entry_meta.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

TIP_FLOOR_URL = "https://bundles.jito.wtf/api/v1/bundles/tip_floor"
CACHE_TTL_S = 60.0
HTTP_TIMEOUT_S = 8.0
LAMPORTS_PER_SOL = 1_000_000_000


class JitoBundleFeed:
    """Singleton-ish lightweight feed. Construct once, share across scanners."""

    def __init__(self, http_timeout_s: float = HTTP_TIMEOUT_S,
                 cache_ttl_s: float = CACHE_TTL_S):
        self._http_timeout = http_timeout_s
        self._cache_ttl = cache_ttl_s
        self._last_fetch_mono: float = 0.0
        self._last_snapshot: dict = {}

    async def _fetch_tip_floor(self) -> Optional[dict]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    TIP_FLOOR_URL,
                    timeout=aiohttp.ClientTimeout(total=self._http_timeout),
                ) as resp:
                    if resp.status != 200:
                        logger.debug(f"[Jito] tip_floor HTTP {resp.status}")
                        return None
                    data = await resp.json(content_type=None)
                    if isinstance(data, list) and data:
                        return data[-1]  # latest sample
                    if isinstance(data, dict):
                        return data
                    return None
        except Exception as e:
            logger.debug(f"[Jito] tip_floor fetch err: {e}")
            return None

    @staticmethod
    def _sol_to_lamports(v) -> Optional[int]:
        if v is None:
            return None
        try:
            return int(round(float(v) * LAMPORTS_PER_SOL))
        except Exception:
            return None

    async def snapshot(self) -> dict:
        """Return current Jito macro features (cached). Fail-open to {}."""
        now = time.monotonic()
        if now - self._last_fetch_mono < self._cache_ttl and self._last_snapshot:
            return self._last_snapshot
        sample = await self._fetch_tip_floor()
        if not sample:
            return self._last_snapshot  # last known or {}

        # Schema as of 2026-05: keys are SOL values, named
        # landed_tips_<pct>_percentile (e.g. 25th, 50th, 75th, 95th, 99th).
        floor_sol = (sample.get("landed_tips_25th_percentile")
                     or sample.get("ema_landed_tips_50th_percentile"))
        p99_sol = sample.get("landed_tips_99th_percentile")
        p50_sol = (sample.get("landed_tips_50th_percentile")
                   or sample.get("ema_landed_tips_50th_percentile"))

        snap = {
            "jito_tip_floor_lamports": self._sol_to_lamports(floor_sol),
            "jito_tip_p50_lamports":   self._sol_to_lamports(p50_sol),
            "jito_tip_p99_lamports":   self._sol_to_lamports(p99_sol),
            "jito_sample_time":        sample.get("time"),
        }
        # Only update cache if we got *some* signal — avoid clobbering
        # the last good snapshot with a partial parse.
        if any(v is not None for k, v in snap.items() if k.endswith("_lamports")):
            self._last_snapshot = snap
            self._last_fetch_mono = now
        return self._last_snapshot or snap


# Module-level singleton for convenience
_default_feed: Optional[JitoBundleFeed] = None


def get_default_feed() -> JitoBundleFeed:
    global _default_feed
    if _default_feed is None:
        _default_feed = JitoBundleFeed()
    return _default_feed


async def _smoke():
    """Smoke test: print current snapshot. Run with `python -m feeds.jito_bundle_feed`."""
    feed = JitoBundleFeed()
    snap = await feed.snapshot()
    print(snap)


if __name__ == "__main__":
    asyncio.run(_smoke())
