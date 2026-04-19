# test_scalp_queue.py
import pytest
import pytest_asyncio
import time
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from core.scalp_capital import ScalpCapitalManager
from feeds.scalp_queue import ScalpQueue
from feeds.candle_utils import Candle
from feeds.setup_detector import TriggerSignal


def _cfg(**overrides):
    c = MagicMock()
    c.scalp_position_usd = 200.0
    c.scalp_max_watch_candidates = 40
    c.scalp_watch_expiry_minutes = 30.0
    c.scalp_stop_cooldown_minutes = 45.0
    c.scalp_max_deployment_pct = 0.80
    c.scalp_min_m5_volume_usd = 50_000
    c.scalp_min_liquidity_usd = 30_000
    c.scalp_min_age_minutes = 5
    c.scalp_max_age_hours = 6.0
    c.scalp_rug_lp_drop_pct = 10.0
    c.scalp_impulse_min_pct = 10.0
    c.scalp_impulse_max_pct = 30.0
    c.scalp_impulse_lookback = 6
    c.scalp_pullback_min_pct = 30.0
    c.scalp_pullback_max_pct = 60.0
    c.scalp_sweep_vol_mult = 1.5
    c.scalp_sweep_vol_lookback = 20
    c.scalp_tp1_pct = 10.0
    c.scalp_tp1_sell = 0.50
    c.scalp_tp2_pct = 15.0
    c.scalp_tp2_sell = 0.35
    c.scalp_stop_pct = 6.0
    c.scalp_min_rr = 2.0
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


def _good_pair(addr="TOKEN1", pool="POOL1"):
    return {
        "chainId": "solana",
        "baseToken": {"address": addr, "symbol": "TEST"},
        "pairAddress": pool,
        "volume": {"m5": 60_000, "h24": 500_000, "h1": 100_000},
        "liquidity": {"usd": 50_000},
        "priceChange": {"m5": 1.0, "h24": 5.0, "h6": 3.0},
        "priceUsd": "1.0",
        "pairCreatedAt": time.time() * 1000 - 30 * 60 * 1000,  # 30 min old
    }


def test_candidate_gate_passes_good_pair():
    q = _make_queue()
    assert q._passes_candidate_gates(_good_pair()) is True


def test_candidate_gate_rejects_low_m5_volume():
    q = _make_queue()
    p = _good_pair()
    p["volume"]["m5"] = 10_000
    assert q._passes_candidate_gates(p) is False


def test_candidate_gate_rejects_low_liquidity():
    q = _make_queue()
    p = _good_pair()
    p["liquidity"]["usd"] = 5_000
    assert q._passes_candidate_gates(p) is False


def test_candidate_gate_accepts_any_age():
    # Age gate removed — 4-phase detector evaluates structure, not freshness.
    q = _make_queue()
    p = _good_pair()
    p["pairCreatedAt"] = time.time() * 1000 - 60_000  # 1 min
    assert q._passes_candidate_gates(p) is True
    p = _good_pair()
    p["pairCreatedAt"] = time.time() * 1000 - 30 * 24 * 3600 * 1000  # 30 days
    assert q._passes_candidate_gates(p) is True


def test_rug_detected_from_lp_drop():
    q = _make_queue()
    q._lp_history["POOL1"] = (time.monotonic() - 300, 50_000)
    p = _good_pair()
    p["liquidity"]["usd"] = 40_000  # 20% drop
    assert q._is_rug("POOL1", p) is True


def test_rug_not_triggered_on_small_drop():
    q = _make_queue()
    q._lp_history["POOL1"] = (time.monotonic() - 300, 50_000)
    p = _good_pair()
    p["liquidity"]["usd"] = 48_000  # 4% drop
    assert q._is_rug("POOL1", p) is False


@pytest.mark.asyncio
async def test_no_trade_when_sol_bearish():
    q = _make_queue()
    q._sol_is_bearish = True  # set by regime check
    await q._maybe_fire_entry("TOKEN1", _good_pair(), signal=_fake_signal())
    assert q.trader.buy.await_count == 0


@pytest.mark.asyncio
async def test_no_trade_when_majority_red():
    q = _make_queue()
    q._majority_red = True
    await q._maybe_fire_entry("TOKEN1", _good_pair(), signal=_fake_signal())
    assert q.trader.buy.await_count == 0


@pytest.mark.asyncio
async def test_no_trade_when_deployment_cap_reached():
    q = _make_queue()
    # Fill capital to 80% deployment
    q.scalp_capital._open["A"] = 800.0
    q.scalp_capital._open["B"] = 800.0  # 1600 / 2000 = 80%
    await q._maybe_fire_entry("TOKEN1", _good_pair(), signal=_fake_signal())
    assert q.trader.buy.await_count == 0


@pytest.mark.asyncio
async def test_fires_entry_and_attaches_scalp_meta():
    q = _make_queue()
    sig = _fake_signal()
    pair = _good_pair()
    q._open_positions_ref[pair["baseToken"]["address"].lower()] = MagicMock()  # simulate fill
    await q._maybe_fire_entry("TOKEN1", pair, signal=sig)
    q.trader.buy.assert_awaited_once()
    _, kw = q.trader.buy.call_args
    assert kw["strategy"] == "scalp"
    assert kw["scalp_meta"]["sweep_low"] == sig.sweep_low
    assert kw["scalp_meta"]["stop_price"] == sig.stop_price
    assert kw["scalp_meta"]["tp1_price"] == sig.tp1_price


# ── helpers ──

def _fake_signal():
    return TriggerSignal(
        symbol="TEST",
        entry_price=1.0,
        stop_price=0.94,
        tp1_price=1.10,
        sweep_low=0.94,
        reason="impulse=15.0% pullback=40% sweep_vol=2.00x rr=2.5",
    )


def _make_queue(**cfg_overrides):
    trader = MagicMock()
    trader.buy = AsyncMock()
    capital = ScalpCapitalManager(max_concurrent=5)
    open_refs = {}
    cfg = _cfg(**cfg_overrides)
    ohlcv = AsyncMock()
    ohlcv.fetch_5m = AsyncMock(return_value=[])  # default empty
    q = ScalpQueue(
        trader=trader,
        open_positions_ref=open_refs,
        scalp_capital=capital,
        config=cfg,
        ohlcv_client=ohlcv,
    )
    q._open_positions_ref = open_refs  # expose for tests
    return q
