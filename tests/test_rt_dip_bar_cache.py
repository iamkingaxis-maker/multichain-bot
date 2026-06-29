# tests/test_rt_dip_bar_cache.py
import asyncio
import pytest
from feeds.dip_scanner import DipScanner
from feeds.candle_utils import Candle


def _mk_scanner():
    # DipScanner has heavy deps; construct without __init__ for a unit cache test.
    s = DipScanner.__new__(DipScanner)
    s._rt_dip_bar_cache = {}
    return s


def test_cache_hit_skips_fetch():
    s = _mk_scanner()
    # A client that would blow up if called proves the cache short-circuits.
    class BoomClient:
        async def fetch_1m(self, pool, limit=5):
            raise AssertionError("should not be called on a fresh cache hit")
    s.dexs_client = BoomClient()
    s._rt_dip_bar_cache["AAA"] = ([{"ts_ms": 1000, "high": 2.0, "low": 1.0}], 1000.0)

    async def run():
        return await s._get_rt_dip_bars("AAA", "ray", "pair", ttl_secs=60.0, now=1030.0)

    assert asyncio.run(run()) == [{"ts_ms": 1000, "high": 2.0, "low": 1.0}]


def test_cache_miss_fetches_via_client_and_converts():
    # Bars come from the canonical DexScreenerClient.fetch_1m (Candle objects);
    # _get_rt_dip_bars converts them to {ts_ms, high, low} dicts (open_time is in
    # SECONDS -> ts_ms = *1000) and caches the converted result.
    s = _mk_scanner()

    class FakeClient:
        def __init__(self):
            self.calls = []
        async def fetch_1m(self, pool, limit=5):
            self.calls.append((pool, limit))
            return [
                Candle(open_time=1700, open=1.0, high=9.0, low=8.0, close=8.5, volume=1.0, close_time=1760),
                Candle(open_time=1760, open=8.5, high=8.7, low=7.5, close=7.6, volume=2.0, close_time=1820),
            ]

    s.dexs_client = FakeClient()

    async def run():
        return await s._get_rt_dip_bars("BBB", "ray", "poolXYZ", ttl_secs=60.0, now=2000.0)

    bars = asyncio.run(run())
    assert bars == [
        {"ts_ms": 1_700_000, "high": 9.0, "low": 8.0, "close": 8.5},
        {"ts_ms": 1_760_000, "high": 8.7, "low": 7.5, "close": 7.6},
    ]
    assert s._rt_dip_bar_cache["BBB"][0] == bars
    assert s.dexs_client.calls and s.dexs_client.calls[0][0] == "poolXYZ"


def test_fetch_failure_returns_stale_cache():
    s = _mk_scanner()
    s._rt_dip_bar_cache["CCC"] = ([{"ts_ms": 1000, "high": 2.0, "low": 1.0}], 100.0)

    class BoomClient:
        async def fetch_1m(self, pool, limit=5):
            raise RuntimeError("io.dx down")

    s.dexs_client = BoomClient()

    async def run():
        return await s._get_rt_dip_bars("CCC", "ray", "pair", ttl_secs=1.0, now=10_000.0)

    bars = asyncio.run(run())
    assert bars == [{"ts_ms": 1000, "high": 2.0, "low": 1.0}]  # stale cache, no raise


def test_empty_candles_returns_empty_no_cache_write():
    # Client returns nothing (e.g. circuit open) -> [] and no positive cache entry.
    s = _mk_scanner()

    class EmptyClient:
        async def fetch_1m(self, pool, limit=5):
            return []

    s.dexs_client = EmptyClient()

    async def run():
        return await s._get_rt_dip_bars("DDD", "ray", "pair", ttl_secs=60.0, now=5000.0)

    assert asyncio.run(run()) == []
    assert "DDD" not in s._rt_dip_bar_cache


def test_no_client_returns_empty():
    s = _mk_scanner()
    s.dexs_client = None

    async def run():
        return await s._get_rt_dip_bars("EEE", "ray", "pair", ttl_secs=60.0, now=6000.0)

    assert asyncio.run(run()) == []
