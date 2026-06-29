# tests/test_rt_dip_bar_cache.py
import asyncio
import pytest
from feeds.dip_scanner import DipScanner


def _mk_scanner():
    # DipScanner has heavy deps; construct without __init__ for a unit cache test.
    s = DipScanner.__new__(DipScanner)
    s._rt_dip_bar_cache = {}
    return s


def test_cache_hit_skips_fetch(monkeypatch):
    s = _mk_scanner()
    calls = {"n": 0}

    async def fake_fetch(self, addr, dex_slug, pair_addr, *, res="1m", ttl_secs=60.0, now=None):
        # call the REAL method but stub the network layer it uses
        raise AssertionError("should not be called")

    s._rt_dip_bar_cache["AAA"] = ([{"ts_ms": 1, "high": 2.0, "low": 1.0}], 1000.0)

    async def run():
        bars = await s._get_rt_dip_bars("AAA", "ray", "pair", ttl_secs=60.0, now=1030.0)
        return bars

    bars = asyncio.run(run())
    assert bars == [{"ts_ms": 1, "high": 2.0, "low": 1.0}]


def test_cache_miss_fetches_and_caches(monkeypatch):
    s = _mk_scanner()
    parsed = [{"ts_ms": 5, "high": 9.0, "low": 8.0}]

    async def fake_run_ds_fetch(fn, arg):
        return b"rawbytes"

    monkeypatch.setattr("feeds.dip_scanner.run_ds_fetch", fake_run_ds_fetch, raising=False)
    monkeypatch.setattr("feeds.dip_scanner.parse_chart_bars", lambda raw: parsed, raising=False)

    async def run():
        return await s._get_rt_dip_bars("BBB", "ray", "pair", ttl_secs=60.0, now=2000.0)

    bars = asyncio.run(run())
    assert bars == parsed
    assert s._rt_dip_bar_cache["BBB"][0] == parsed


def test_slug_ladder_falls_through_to_alternate(monkeypatch):
    # The dexId->slug map misses often; _get_rt_dip_bars must try a fallback
    # ladder and bail on the first slug that yields bars (single-slug coverage
    # was ~10% -> BUFFER_ONLY fleet-wide).
    s = _mk_scanner()
    parsed = [{"ts_ms": 5, "high": 9.0, "low": 8.0}]
    tried = []

    async def fake_run_ds_fetch(fn, slug):
        tried.append(slug)
        return b"good" if slug == "solamm" else b""  # only the alternate works

    def fake_parse(raw):
        return parsed if raw == b"good" else []

    monkeypatch.setattr("feeds.dip_scanner.run_ds_fetch", fake_run_ds_fetch, raising=False)
    monkeypatch.setattr("feeds.dip_scanner.parse_chart_bars", fake_parse, raising=False)

    async def run():
        return await s._get_rt_dip_bars("DDD", "pumpfundex", "pair", ttl_secs=60.0, now=3000.0)

    bars = asyncio.run(run())
    assert bars == parsed
    assert tried[0] == "pumpfundex"      # primary tried first
    assert "solamm" in tried             # fell through to a working alternate
    assert s._rt_dip_bar_cache["DDD"][0] == parsed


def test_slug_ladder_capped_at_three_attempts(monkeypatch):
    # Never hammer more than 3 slugs in one call.
    s = _mk_scanner()
    tried = []

    async def fake_run_ds_fetch(fn, slug):
        tried.append(slug)
        return b""  # nothing ever works -> exhaust the cap, return []

    monkeypatch.setattr("feeds.dip_scanner.run_ds_fetch", fake_run_ds_fetch, raising=False)
    monkeypatch.setattr("feeds.dip_scanner.parse_chart_bars", lambda raw: [], raising=False)

    async def run():
        return await s._get_rt_dip_bars("EEE", "pumpfundex", "pair", ttl_secs=60.0, now=4000.0)

    bars = asyncio.run(run())
    assert bars == []
    assert len(tried) == 3  # capped


def test_fetch_failure_returns_stale_cache(monkeypatch):
    s = _mk_scanner()
    s._rt_dip_bar_cache["CCC"] = ([{"ts_ms": 1, "high": 2.0, "low": 1.0}], 100.0)

    async def boom(fn, arg):
        raise RuntimeError("io.dx down")

    monkeypatch.setattr("feeds.dip_scanner.run_ds_fetch", boom, raising=False)

    async def run():
        return await s._get_rt_dip_bars("CCC", "ray", "pair", ttl_secs=1.0, now=10_000.0)

    bars = asyncio.run(run())
    assert bars == [{"ts_ms": 1, "high": 2.0, "low": 1.0}]  # stale cache, no raise
