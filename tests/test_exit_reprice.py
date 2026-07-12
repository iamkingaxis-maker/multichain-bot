# -*- coding: utf-8 -*-
"""EXIT-REPRICE fresh-floor (exit-side twin of BUY-REPRICE, 2026-06-28).

The in-flight loss floor (kind=IN_FLIGHT_FLOOR, floor=-7%) is evaluated ONLY on
the slow ~150s main sweep. A fresh ~3s Jupiter price already exists in
self._fast_samples but is used for ENTRIES only — open positions' floor is blind
to it, so the floor gaps through (slow_tick_pnl=-3% while fresh_pnl=-9%).

EXIT_REPRICE_MODE=off|shadow|enforce (default off) runs the SAME in-flight-floor
check on the fast tick against the fresh price. Default off = byte-identical:
_reprice_exit_floors returns immediately (no shadow records, no sells)."""
import asyncio
import os

import pytest

from types import SimpleNamespace as NS

from core.fast_watch import rt_mode, exit_reprice_would_fire
from feeds.dip_scanner import DipScanner


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---- flag resolver -------------------------------------------------------

@pytest.mark.parametrize("val,expected", [
    ("off", "off"), ("shadow", "shadow"), ("enforce", "enforce"),
    ("garbage", "off"), ("SHADOW", "shadow"), ("  Enforce ", "enforce"),
])
def test_flag_resolver(monkeypatch, val, expected):
    monkeypatch.setenv("EXIT_REPRICE_MODE", val)
    assert rt_mode("EXIT_REPRICE_MODE") == expected


def test_flag_resolver_default_off(monkeypatch):
    monkeypatch.delenv("EXIT_REPRICE_MODE", raising=False)
    assert rt_mode("EXIT_REPRICE_MODE") == "off"


# ---- pure floor-fires logic on a fresh-vs-slow gap -----------------------

def test_would_fire_confirm_ticks_satisfied():
    # entry=1.0; fast samples bled -3% then -8% then -9% (slow_tick saw only -3%).
    # confirm_ticks=2 -> the two NEWEST samples (-8%, -9%) are both <= -7% floor.
    samples = [0.97, 0.92, 0.91]   # pnl: -3%, -8%, -9%
    fires, fresh_pnl, why = exit_reprice_would_fire(
        samples, entry_price=1.0, peak_pnl_pct=0.5, secs_from_peak=30,
        floor_pct=-7.0, confirm_ticks=2)
    assert fires is True
    assert fresh_pnl == pytest.approx(-9.0, abs=0.01)
    assert why   # non-empty reason (MAE-floor or velocity-bail)


def test_would_fire_mae_floor_reason_with_high_peak():
    # A peak above the velbail ceiling (>=2%) so only the MAE-floor path can fire.
    samples = [0.92, 0.91]   # pnl: -8%, -9%
    fires, fresh_pnl, why = exit_reprice_would_fire(
        samples, entry_price=1.0, peak_pnl_pct=5.0, secs_from_peak=30,
        floor_pct=-7.0, confirm_ticks=2)
    assert fires is True
    assert "floor" in why.lower()


def test_would_fire_wick_guard_single_subfloor():
    # Only ONE sub-floor sample (the newest -9%); the prior is -3% (above floor).
    # confirm_ticks=2 -> wick guard blocks (would-fire=False).
    samples = [0.97, 0.91]         # pnl: -3%, -9%
    fires, fresh_pnl, why = exit_reprice_would_fire(
        samples, entry_price=1.0, peak_pnl_pct=0.5, secs_from_peak=30,
        floor_pct=-7.0, confirm_ticks=2)
    assert fires is False
    assert fresh_pnl == pytest.approx(-9.0, abs=0.01)


def test_would_fire_above_floor_does_not_fire():
    samples = [0.99, 0.98, 0.985]  # pnl ~ -1..-2%, never below -7%
    fires, fresh_pnl, why = exit_reprice_would_fire(
        samples, entry_price=1.0, peak_pnl_pct=0.5, secs_from_peak=30,
        floor_pct=-7.0, confirm_ticks=2)
    assert fires is False


def test_would_fire_bad_data_failsafe():
    assert exit_reprice_would_fire([], 1.0, 0.0, 10)[0] is False
    assert exit_reprice_would_fire([0.9], 0.0, 0.0, 10)[0] is False   # entry<=0
    assert exit_reprice_would_fire([0.0, 0.0], 1.0, 0.0, 10)[0] is False  # dead price


# ---- DipScanner._reprice_exit_floors integration -------------------------

def _scanner_with_position(entry=1.0, samples=(0.92, 0.91), tp1_hit=False,
                           bot_id="badday_flush", addr="mintTOK"):
    from collections import deque
    sc = DipScanner.__new__(DipScanner)
    pos = NS(token="TOK", address=addr, pair_address="pairTOK",
             entry_price=entry, peak_pnl_pct=0.5, peak_pnl_at_secs=0,
             entry_time=0.0, tp1_hit=tp1_hit, state_blob={})

    class _PM:
        def __init__(self):
            self.config = NS(bot_id=bot_id)
        def iter_positions(self):
            return [pos]

    sc.bot_position_managers = {bot_id: _PM()}
    sc._fast_samples = {addr: deque(samples, maxlen=20)}
    sc._fast_samples_ts = {}
    sc._exit_reprice_shadow_recs = []
    sc._sell_calls = []

    def _append(rec):
        sc._exit_reprice_shadow_recs.append(rec)
    sc._append_exit_reprice_shadow = _append

    async def _sell(bot_id, token, decision, price, now, exit_cadence="main"):
        # exit_cadence: the fast-tick enforce path stamps "fastwatch" (post-TP1
        # fast-watch forward grade, 2026-07-12) — the stub mirrors the signature.
        sc._sell_calls.append((bot_id, token, decision, price, now))
    sc._execute_bot_sell = _sell

    async def _batch(addrs):
        return {a.lower(): 0.91 for a in addrs}   # fresh sub-floor price
    sc._fast_batch_prices = _batch
    return sc, pos


def test_off_is_noop(monkeypatch):
    monkeypatch.delenv("EXIT_REPRICE_MODE", raising=False)
    sc, pos = _scanner_with_position()
    cfg = NS(sample_window=20)
    prices = {"minttok": 0.91}
    _run(sc._reprice_exit_floors(cfg, prices, now=100.0))
    assert sc._exit_reprice_shadow_recs == []
    assert sc._sell_calls == []
    assert pos.state_blob == {}


def test_shadow_records_no_sell(monkeypatch):
    monkeypatch.setenv("EXIT_REPRICE_MODE", "shadow")
    monkeypatch.setenv("EXIT_REPRICE_CONFIRM_TICKS", "2")
    sc, pos = _scanner_with_position(samples=(0.92, 0.91))  # -8%, -9%
    cfg = NS(sample_window=20)
    prices = {"minttok": 0.91}
    _run(sc._reprice_exit_floors(cfg, prices, now=100.0))
    assert len(sc._exit_reprice_shadow_recs) == 1
    assert sc._sell_calls == []                      # NEVER sells in shadow
    assert pos.state_blob.get("iff_fired") is not True


def test_shadow_wick_guard_no_record(monkeypatch):
    monkeypatch.setenv("EXIT_REPRICE_MODE", "shadow")
    monkeypatch.setenv("EXIT_REPRICE_CONFIRM_TICKS", "2")
    # one sub-floor sample only -> wick guard blocks
    sc, pos = _scanner_with_position(samples=(0.97, 0.91))  # -3%, -9%
    cfg = NS(sample_window=20)
    prices = {"minttok": 0.91}
    _run(sc._reprice_exit_floors(cfg, prices, now=100.0))
    assert sc._exit_reprice_shadow_recs == []
    assert sc._sell_calls == []


def test_enforce_routes_sell(monkeypatch):
    monkeypatch.setenv("EXIT_REPRICE_MODE", "enforce")
    monkeypatch.setenv("EXIT_REPRICE_CONFIRM_TICKS", "2")
    sc, pos = _scanner_with_position(samples=(0.92, 0.91))
    cfg = NS(sample_window=20)
    prices = {"minttok": 0.91}
    _run(sc._reprice_exit_floors(cfg, prices, now=100.0))
    assert len(sc._sell_calls) == 1
    bot_id, token, decision, price, now = sc._sell_calls[0]
    assert decision.kind == "IN_FLIGHT_FLOOR"
    assert decision.sell_fraction == 1.0
    assert price == pytest.approx(0.91)
    assert pos.state_blob.get("iff_fired") is True


def test_enforce_skips_non_badday(monkeypatch):
    monkeypatch.setenv("EXIT_REPRICE_MODE", "enforce")
    sc, pos = _scanner_with_position(bot_id="timebox_probe", samples=(0.92, 0.91))
    cfg = NS(sample_window=20)
    prices = {"minttok": 0.91}
    _run(sc._reprice_exit_floors(cfg, prices, now=100.0))
    assert sc._sell_calls == []


def test_enforce_skips_post_tp1(monkeypatch):
    monkeypatch.setenv("EXIT_REPRICE_MODE", "enforce")
    sc, pos = _scanner_with_position(tp1_hit=True, samples=(0.92, 0.91))
    cfg = NS(sample_window=20)
    prices = {"minttok": 0.91}
    _run(sc._reprice_exit_floors(cfg, prices, now=100.0))
    assert sc._sell_calls == []


def test_enforce_idempotent_with_slow_tick(monkeypatch):
    monkeypatch.setenv("EXIT_REPRICE_MODE", "enforce")
    sc, pos = _scanner_with_position(samples=(0.92, 0.91))
    pos.state_blob["iff_fired"] = True   # slow tick (or prior) already fired
    cfg = NS(sample_window=20)
    prices = {"minttok": 0.91}
    _run(sc._reprice_exit_floors(cfg, prices, now=100.0))
    assert sc._sell_calls == []
