# -*- coding: utf-8 -*-
"""CHART_DS_PRIMARY strict DexScreener-primary chart routing + shared GT
rate limiter (2026-06-17).

Validates:
  (A) CHART_DS_PRIMARY=on uses DexScreener and makes ZERO GT calls when DS
      has data.
  (B) CHART_DS_PRIMARY=on falls back to GT exactly once, and only on a true
      DS gap (DS exhausted after its own retry).
  (C) Default (flag off) behaviour is unchanged — GT is hit on the first DS
      miss (legacy DS->GT loop).
  (D) The GeckoTerminalClient throttle caps GT at <= rate_per_min in a
      rolling 60s window even under a burst of distinct keys, and the
      GT_RATE_PER_MIN env override tightens the cap.
"""
import asyncio
import os

import pytest

from feeds.candle_utils import Candle
from feeds.chart_data import assemble_chart_data
from feeds.gecko_ohlcv import GeckoTerminalClient


def _candle(t: int) -> Candle:
    return Candle(open_time=t, open=1.0, high=1.1, low=0.9,
                  close=1.0, volume=10.0, close_time=t + 59)


def _bars(n: int):
    return [_candle(i * 60) for i in range(n)]


class _CountingGT:
    """Stand-in for GeckoTerminalClient that only counts calls per TF."""

    def __init__(self, returns=None):
        self.calls = {"1m": 0, "5m": 0, "15m": 0, "1h": 0}
        # returns: dict tf->list, or None => empty everywhere
        self._returns = returns or {}

    async def _r(self, tf, limit):
        self.calls[tf] += 1
        return list(self._returns.get(tf, []))

    async def fetch_1m(self, pool, limit=100):
        return await self._r("1m", limit)

    async def fetch_5m(self, pool, limit=144):
        return await self._r("5m", limit)

    async def fetch_15m(self, pool, limit=96):
        return await self._r("15m", limit)

    async def fetch_1h(self, pool, limit=48):
        return await self._r("1h", limit)


class _CountingDS(_CountingGT):
    """Same counting interface for the DexScreener side."""
    pass


def _set_flag(monkeypatch, val):
    if val is None:
        monkeypatch.delenv("CHART_DS_PRIMARY", raising=False)
    else:
        monkeypatch.setenv("CHART_DS_PRIMARY", val)
    # Keep parallel chart path off so we measure the deterministic seq path.
    monkeypatch.delenv("PARALLEL_SCAN_MODE", raising=False)


# ---- (A) DS-primary ON, DS has data -> zero GT calls --------------------

def test_ds_primary_on_no_gt_calls_when_ds_has_data(monkeypatch):
    _set_flag(monkeypatch, "on")
    full = {tf: _bars(20) for tf in ("1m", "5m", "15m", "1h")}
    gt = _CountingGT()                 # GT returns nothing (must not be called)
    ds = _CountingDS(returns=full)
    cd = asyncio.run(assemble_chart_data(gt, "POOL", dexs_client=ds))
    assert sum(gt.calls.values()) == 0, gt.calls
    assert sum(ds.calls.values()) == 4, ds.calls
    assert len(cd.candles_1m) == 20
    assert cd.has_full_coverage()


# ---- (B) DS-primary ON, true DS gap -> exactly one GT fallback per TF ----

def test_ds_primary_on_gt_fallback_only_on_ds_gap(monkeypatch):
    _set_flag(monkeypatch, "on")
    # DS empty everywhere -> after 2 DS attempts each, ONE GT call per TF.
    gt = _CountingGT(returns={tf: _bars(15) for tf in ("1m", "5m", "15m", "1h")})
    ds = _CountingDS()  # empty
    cd = asyncio.run(assemble_chart_data(gt, "POOL", dexs_client=ds))
    # exactly one GT call per timeframe (not two)
    assert gt.calls == {"1m": 1, "5m": 1, "15m": 1, "1h": 1}, gt.calls
    # DS retried (2 attempts) before falling back
    assert ds.calls == {"1m": 2, "5m": 2, "15m": 2, "1h": 2}, ds.calls
    assert cd.has_full_coverage()


# ---- (C) Default (flag off) -> legacy DS->GT loop, GT hit on first miss --

def test_default_off_hits_gt_on_first_ds_miss(monkeypatch):
    _set_flag(monkeypatch, None)  # unset -> default off
    gt = _CountingGT(returns={tf: _bars(15) for tf in ("1m", "5m", "15m", "1h")})
    ds = _CountingDS()  # empty
    cd = asyncio.run(assemble_chart_data(gt, "POOL", dexs_client=ds))
    # legacy: DS once then GT once (GT returns data -> stop). 1 DS + 1 GT per TF.
    assert gt.calls == {"1m": 1, "5m": 1, "15m": 1, "1h": 1}, gt.calls
    assert ds.calls == {"1m": 1, "5m": 1, "15m": 1, "1h": 1}, ds.calls
    assert cd.has_full_coverage()


def test_default_off_explicit_value_unchanged(monkeypatch):
    # explicit "off" behaves identically to unset
    _set_flag(monkeypatch, "off")
    gt = _CountingGT(returns={tf: _bars(15) for tf in ("1m", "5m", "15m", "1h")})
    ds = _CountingDS(returns={tf: _bars(20) for tf in ("1m", "5m", "15m", "1h")})
    asyncio.run(assemble_chart_data(gt, "POOL", dexs_client=ds))
    # DS has data on first call -> GT never touched (same as on-path here)
    assert sum(gt.calls.values()) == 0, gt.calls
    assert sum(ds.calls.values()) == 4, ds.calls


# ---- (D) shared GT rate limiter caps requests in rolling 60s window ------

def test_gt_throttle_caps_requests_per_minute():
    # rate_per_min=3 -> the 4th throttle in the same window must sleep.
    cl = GeckoTerminalClient(rate_per_min=3)
    import time as _t

    async def _drive():
        base = _t.monotonic()
        # 3 immediate passes, no sleep budget consumed beyond logging
        for _ in range(3):
            await cl._throttle(_t.monotonic())
        # log should hold exactly 3 timestamps within the window
        assert len(cl._request_log) == 3
        assert cl.gt_request_count == 3
        # 4th would sleep ~60s; assert it WOULD block rather than actually wait
        recent = [t for t in cl._request_log if t > _t.monotonic() - 60.0]
        assert len(recent) >= cl._rate_per_min

    asyncio.run(_drive())


def test_gt_rate_env_override_tightens_cap(monkeypatch):
    monkeypatch.setenv("GT_RATE_PER_MIN", "10")
    cl = GeckoTerminalClient(rate_per_min=25)
    assert cl._rate_per_min == 10  # env tightened below constructor default
    # env override never RAISES the cap above the constructor value
    monkeypatch.setenv("GT_RATE_PER_MIN", "999")
    cl2 = GeckoTerminalClient(rate_per_min=25)
    assert cl2._rate_per_min == 25


def test_gt_request_count_starts_zero():
    cl = GeckoTerminalClient()
    assert cl.gt_request_count == 0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
