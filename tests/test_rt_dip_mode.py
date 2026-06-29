import os
import pytest
from core.realtime_dip import RollingPriceWindow
from feeds.dip_scanner import DipScanner


def _scanner():
    s = DipScanner.__new__(DipScanner)
    s._rt_dip_bar_cache = {}
    s._rt_dip_windows = {}
    return s


def _apply(s, pair, snap_price, fresh_price, mode, bars=None, now=2_000_000_000.0):
    """Drive the extracted RT_DIP helper directly (pure dispatch)."""
    return s._apply_rt_dip(pair, snap_price, fresh_price, mode, bars=bars or [], now=now)


def test_off_is_byte_identical():
    s = _scanner()
    pair = {"priceChange": {"h1": -10.0, "m5": -2.0}}
    before = dict(pair["priceChange"])
    _apply(s, pair, snap_price=1.0, fresh_price=1.0, mode="off")
    assert pair["priceChange"] == before


def test_shadow_does_not_mutate_pricechange():
    s = _scanner()
    pair = {"priceChange": {"h1": -10.0, "m5": -2.0}}
    before = dict(pair["priceChange"])
    # seed a window high so a real-time pc exists
    w = RollingPriceWindow(); w.append(2_000_000_000.0 - 60, 2.0)
    s._rt_dip_windows["AAA"] = w
    _apply(s, {"address": "AAA", **pair}, snap_price=1.0, fresh_price=1.0, mode="shadow")
    assert pair["priceChange"] == before


def test_enforce_overwrites_when_usable():
    s = _scanner()
    pair = {"address": "AAA", "priceChange": {"h1": -10.0, "m5": -2.0}}
    bars = [{"ts_ms": (2_000_000_000.0 - 1800) * 1000.0, "high": 4.0, "low": 3.0}]
    s._rt_dip_windows["AAA"] = RollingPriceWindow()
    s._rt_dip_windows["AAA"].append(2_000_000_000.0 - 1, 1.0)
    _apply(s, pair, snap_price=1.0, fresh_price=1.0, mode="enforce", bars=bars)
    # h1 sees bar high 4.0 -> -75% (overwrote -10.0)
    assert pair["priceChange"]["h1"] == -75.0


def test_enforce_none_leaves_pricechange_untouched():
    s = _scanner()
    pair = {"address": "AAA", "priceChange": {"h1": -10.0}}
    s._rt_dip_windows["AAA"] = RollingPriceWindow()  # empty -> coverage NONE
    _apply(s, pair, snap_price=1.0, fresh_price=1.0, mode="enforce", bars=[])
    assert pair["priceChange"]["h1"] == -10.0  # fell back, not fabricated
