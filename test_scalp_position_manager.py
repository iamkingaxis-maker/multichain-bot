"""
Unit tests for the PositionManager scalp branch after the 4-phase rewrite.
Tests: TP1 +10%/50%, TP2 +15%/35% of remainder, 6% hard stop,
time-exit (no +5% in 4 candles), runner via winner_trail_pct.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta
from core.position_manager import PositionManager, PositionState, MarketConditionMonitor


def _scalp_state(pnl_pct=0.0, tp1_hit=False, tp2_hit=False, minutes_open=0,
                 sweep_low=0.94, entry_close_time=None):
    entry_price = 1.0
    now = datetime.now(timezone.utc)
    if entry_close_time is None:
        entry_close_time = int((now - timedelta(minutes=minutes_open)).timestamp())
    state = PositionState(
        token_address="ADDR1",
        token_symbol="TEST",
        chain_id="solana",
        entry_price=entry_price,
        entry_volume_usd=0.0,
        position_size_usd=200.0,
        original_size_usd=200.0,
        entry_time=now - timedelta(minutes=minutes_open),
        strategy="scalp",
        current_price=entry_price * (1 + pnl_pct / 100),
        peak_price=max(entry_price, entry_price * (1 + pnl_pct / 100)),
        tp1_hit=tp1_hit,
        tp2_hit=tp2_hit,
    )
    state.scalp_meta = {
        "sweep_low": sweep_low,
        "stop_price": 0.94,
        "tp1_price": 1.10,
        "entry_close_time": entry_close_time,
    }
    return state


def _mgr(**overrides):
    trader = MagicMock()
    trader.open_positions = {}
    mgr = PositionManager(
        chain_name="Solana", chain_id="solana",
        trader=trader,
        open_positions_ref=trader.open_positions,
        telegram=MagicMock(),
        tracker=MagicMock(),
        market_monitor=MarketConditionMonitor(),
        scalp_tp1_pct=overrides.get("scalp_tp1_pct", 10.0),
        scalp_tp1_sell=overrides.get("scalp_tp1_sell", 0.50),
        scalp_tp2_pct=overrides.get("scalp_tp2_pct", 15.0),
        scalp_tp2_sell=overrides.get("scalp_tp2_sell", 0.35),
        scalp_stop_pct=overrides.get("scalp_stop_pct", 6.0),
        scalp_time_exit_candles=overrides.get("scalp_time_exit_candles", 4),
        scalp_time_exit_min_pct=overrides.get("scalp_time_exit_min_pct", 5.0),
    )
    mgr._execute_sell = AsyncMock()
    return mgr


@pytest.mark.asyncio
async def test_scalp_tp1_fires_at_10pct():
    mgr = _mgr()
    s = _scalp_state(pnl_pct=10.1)
    await mgr._evaluate_scalp("ADDR1", s)
    mgr._execute_sell.assert_awaited_once()
    _, kw = mgr._execute_sell.call_args
    assert kw["pct"] == pytest.approx(0.50)
    assert s.tp1_hit is True


@pytest.mark.asyncio
async def test_scalp_tp2_fires_after_tp1():
    mgr = _mgr()
    s = _scalp_state(pnl_pct=15.5, tp1_hit=True)
    await mgr._evaluate_scalp("ADDR1", s)
    mgr._execute_sell.assert_awaited_once()
    _, kw = mgr._execute_sell.call_args
    assert kw["pct"] == pytest.approx(0.35)
    assert s.tp2_hit is True


@pytest.mark.asyncio
async def test_scalp_hard_stop_at_6pct():
    mgr = _mgr()
    s = _scalp_state(pnl_pct=-6.1)
    await mgr._evaluate_scalp("ADDR1", s)
    mgr._execute_sell.assert_awaited_once()
    _, kw = mgr._execute_sell.call_args
    assert kw["pct"] == 1.0
    assert "stop" in kw["reason"].lower()


@pytest.mark.asyncio
async def test_scalp_time_exit_fires_after_4_candles_without_5pct():
    mgr = _mgr()
    # entry_close_time is 4.1 × 300s ago (≥ 4 candles)
    past = int((datetime.now(timezone.utc) - timedelta(seconds=1260)).timestamp())
    s = _scalp_state(pnl_pct=2.0, entry_close_time=past)
    await mgr._evaluate_scalp("ADDR1", s)
    mgr._execute_sell.assert_awaited_once()
    _, kw = mgr._execute_sell.call_args
    assert kw["pct"] == 1.0
    assert "time" in kw["reason"].lower()


@pytest.mark.asyncio
async def test_scalp_time_exit_suppressed_when_above_5pct():
    mgr = _mgr()
    past = int((datetime.now(timezone.utc) - timedelta(seconds=1260)).timestamp())
    s = _scalp_state(pnl_pct=5.5, entry_close_time=past)
    await mgr._evaluate_scalp("ADDR1", s)
    mgr._execute_sell.assert_not_awaited()


@pytest.mark.asyncio
async def test_scalp_no_tp2_before_tp1():
    mgr = _mgr()
    s = _scalp_state(pnl_pct=15.5, tp1_hit=False)
    await mgr._evaluate_scalp("ADDR1", s)
    # Should take TP1 (gate at 10%), not TP2
    mgr._execute_sell.assert_awaited_once()
    _, kw = mgr._execute_sell.call_args
    assert kw["pct"] == pytest.approx(0.50)
    assert s.tp1_hit is True
    assert s.tp2_hit is False


@pytest.mark.asyncio
async def test_scalp_runner_no_action_between_tp2_and_trail():
    mgr = _mgr()
    s = _scalp_state(pnl_pct=18.0, tp1_hit=True, tp2_hit=True)
    await mgr._evaluate_scalp("ADDR1", s)
    # Runner phase — no further action until stop or external trailing
    mgr._execute_sell.assert_not_awaited()
