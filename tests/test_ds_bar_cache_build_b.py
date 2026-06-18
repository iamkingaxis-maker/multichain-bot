# -*- coding: utf-8 -*-
"""BUILD B (2026-06-17) — chart-bar cross-cycle cache reuse + executor throughput.

Validates:
  (1) CHART_BAR_TTL_SECS default = 60s (unset -> byte-identical legacy TTL);
      a stale bar WITHIN the env-extended TTL is served from cache (no refetch).
  (2) A bar OLDER than the TTL triggers a refetch.
  (3) The 1h per-call override floor (300s) is preserved (env only LENGTHENS).
  (4) DS_FETCH_WORKERS is configurable + clamped to [1, 12]; default 4.
  (5) DS_FETCH_TIMEOUT_SECS is env-tunable; default 5.
"""
import asyncio

import pytest

from feeds.candle_utils import Candle
from feeds.dexscreener_client import DexScreenerClient


def _bars(n):
    return [Candle(open_time=i * 60, open=1.0, high=1.1, low=0.9,
                   close=1.0, volume=10.0, close_time=i * 60 + 59)
            for i in range(n)]


# ---- (1)/(2)/(3) bar-cache TTL --------------------------------------------

def test_bar_ttl_default_is_constructor_60(monkeypatch):
    monkeypatch.delenv("CHART_BAR_TTL_SECS", raising=False)
    cl = DexScreenerClient(cache_ttl=60)
    assert cl._bar_cache_ttl == 60
    # trades cache TTL is untouched (Build B only moves the bar TTL)
    assert cl._cache_ttl == 60


def test_bar_ttl_env_lengthens(monkeypatch):
    monkeypatch.setenv("CHART_BAR_TTL_SECS", "180")
    cl = DexScreenerClient(cache_ttl=60)
    assert cl._bar_cache_ttl == 180
    assert cl._cache_ttl == 60  # trades cache stays at 60


def test_bar_ttl_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("CHART_BAR_TTL_SECS", "notanint")
    cl = DexScreenerClient(cache_ttl=60)
    assert cl._bar_cache_ttl == 60
    monkeypatch.setenv("CHART_BAR_TTL_SECS", "0")
    cl2 = DexScreenerClient(cache_ttl=60)
    assert cl2._bar_cache_ttl == 60


def test_stale_bar_within_ttl_served_from_cache(monkeypatch):
    """A bar aged beyond the legacy 60s but within the extended TTL must be
    served from cache WITHOUT a refetch."""
    monkeypatch.setenv("CHART_BAR_TTL_SECS", "180")
    cl = DexScreenerClient(cache_ttl=60)

    fetch_calls = {"n": 0}

    async def _boom(*a, **k):
        fetch_calls["n"] += 1
        raise AssertionError("must not refetch within TTL")

    # Pre-seed the cache as if a fetch happened 90s ago (stale vs legacy 60s,
    # fresh vs the 180s env TTL). res=5, limit=144 -> key matches fetch_5m.
    import time as _t
    key = "5:POOL:144"
    cl._cache[key] = (_t.monotonic() - 90.0, _bars(20))
    # Any real fetch path would hit _resolve_pool_meta -> patch it to blow up
    # so a cache MISS is loud.
    monkeypatch.setattr(cl, "_resolve_pool_meta", _boom)

    out = asyncio.run(cl.fetch_5m("POOL", limit=144))
    assert len(out) == 20
    assert fetch_calls["n"] == 0  # served from cache, no refetch


def test_bar_older_than_ttl_refetches(monkeypatch):
    """A bar aged beyond the TTL must trigger a refetch (cache expired)."""
    monkeypatch.setenv("CHART_BAR_TTL_SECS", "180")
    cl = DexScreenerClient(cache_ttl=60)

    fetch_calls = {"n": 0}

    async def _miss(pool):
        fetch_calls["n"] += 1
        return None, None  # forces the empty-return path after cache miss

    import time as _t
    key = "5:POOL:144"
    cl._cache[key] = (_t.monotonic() - 200.0, _bars(20))  # older than 180s TTL
    monkeypatch.setattr(cl, "_resolve_pool_meta", _miss)

    out = asyncio.run(cl.fetch_5m("POOL", limit=144))
    assert fetch_calls["n"] == 1  # cache expired -> attempted a refetch
    assert out == []  # meta miss -> empty (GT fallback at caller)


def test_1h_override_floor_preserved(monkeypatch):
    """fetch_1h passes cache_ttl_override=300; with a SHORTER env TTL the
    effective TTL must stay at the 300s floor (env only lengthens)."""
    monkeypatch.setenv("CHART_BAR_TTL_SECS", "120")  # shorter than the 1h 300 floor
    cl = DexScreenerClient(cache_ttl=60)

    fetch_calls = {"n": 0}

    async def _boom(*a, **k):
        fetch_calls["n"] += 1
        raise AssertionError("must not refetch within 300s 1h floor")

    import time as _t
    # 1h fetch: res = aggregate(1)*60 = 60, limit 48 -> key "60:POOL:48"
    key = "60:POOL:48"
    cl._cache[key] = (_t.monotonic() - 200.0, _bars(20))  # >120 env, <300 floor
    monkeypatch.setattr(cl, "_resolve_pool_meta", _boom)

    out = asyncio.run(cl.fetch_1h("POOL", limit=48))
    assert len(out) == 20
    assert fetch_calls["n"] == 0  # 300s floor kept the bar fresh


def test_1h_override_env_can_lengthen_beyond_floor(monkeypatch):
    """A LONGER env TTL lengthens even the 1h path (max of the two)."""
    monkeypatch.setenv("CHART_BAR_TTL_SECS", "600")
    cl = DexScreenerClient(cache_ttl=60)

    async def _boom(*a, **k):
        raise AssertionError("must not refetch within 600s env TTL")

    import time as _t
    key = "60:POOL:48"
    cl._cache[key] = (_t.monotonic() - 400.0, _bars(20))  # >300 floor, <600 env
    monkeypatch.setattr(cl, "_resolve_pool_meta", _boom)

    out = asyncio.run(cl.fetch_1h("POOL", limit=48))
    assert len(out) == 20


# ---- (4) DS_FETCH_WORKERS configurable + clamped --------------------------

def test_ds_workers_default_4(monkeypatch):
    monkeypatch.delenv("DS_FETCH_WORKERS", raising=False)
    cl = DexScreenerClient()
    assert cl._fetch_workers == 4
    assert cl._executor._max_workers == 4


def test_ds_workers_env_configurable(monkeypatch):
    monkeypatch.setenv("DS_FETCH_WORKERS", "10")
    cl = DexScreenerClient()
    assert cl._fetch_workers == 10
    assert cl._executor._max_workers == 10


def test_ds_workers_clamped_to_12(monkeypatch):
    monkeypatch.setenv("DS_FETCH_WORKERS", "99")
    cl = DexScreenerClient()
    assert cl._fetch_workers == 12


def test_ds_workers_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("DS_FETCH_WORKERS", "x")
    cl = DexScreenerClient()
    assert cl._fetch_workers == 4
    monkeypatch.setenv("DS_FETCH_WORKERS", "0")
    cl2 = DexScreenerClient()
    assert cl2._fetch_workers == 4


# ---- (5) DS_FETCH_TIMEOUT_SECS env-tunable --------------------------------

def test_ds_timeout_default_5(monkeypatch):
    monkeypatch.delenv("DS_FETCH_TIMEOUT_SECS", raising=False)
    cl = DexScreenerClient()
    assert cl._fetch_timeout == 5


def test_ds_timeout_env_configurable(monkeypatch):
    monkeypatch.setenv("DS_FETCH_TIMEOUT_SECS", "8")
    cl = DexScreenerClient()
    assert cl._fetch_timeout == 8


def test_ds_timeout_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("DS_FETCH_TIMEOUT_SECS", "nope")
    cl = DexScreenerClient()
    assert cl._fetch_timeout == 5


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
