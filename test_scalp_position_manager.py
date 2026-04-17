# test_scalp_position_manager.py
"""
Unit tests for the PositionManager scalp branch.
Tests TP1 (3%/50%), TP2 (5%/100%), hard stop (2.5%), time stop (45min).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
from core.position_manager import PositionManager, PositionState, MarketConditionMonitor


def make_state(strategy="scalp", pnl_pct_val=0.0, tp1_hit=False, minutes_open=0):
    entry_price = 1.0
    current_price = entry_price * (1 + pnl_pct_val / 100)
    entry_time = datetime.now(timezone.utc) - timedelta(minutes=minutes_open)
    state = PositionState(
        token_address="ADDR1",
        token_symbol="TEST",
        chain_id="solana",
        entry_price=entry_price,
        entry_volume_usd=0.0,
        position_size_usd=200.0,
        original_size_usd=200.0,
        entry_time=entry_time,
        strategy=strategy,
        current_price=current_price,
        peak_price=max(entry_price, current_price),
        tp1_hit=tp1_hit,
    )
    return state


def make_mgr(**kwargs):
    trader = MagicMock()
    trader.open_positions = {}
    mgr = PositionManager(
        chain_name="Solana",
        chain_id="solana",
        trader=trader,
        open_positions_ref=trader.open_positions,
        telegram=MagicMock(),
        tracker=MagicMock(),
        market_monitor=MarketConditionMonitor(),
        scalp_tp1_pct=kwargs.get("scalp_tp1_pct", 3.0),
        scalp_tp2_pct=kwargs.get("scalp_tp2_pct", 5.0),
        scalp_stop_pct=kwargs.get("scalp_stop_pct", 2.5),
        scalp_max_hold_minutes=kwargs.get("scalp_max_hold_minutes", 45.0),
    )
    mgr._execute_sell = AsyncMock()
    return mgr


@pytest.mark.asyncio
async def test_scalp_tp1_fires_at_3pct():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=3.0)
    assert state.tp1_hit is False
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_awaited_once()
    call_args = mgr._execute_sell.call_args
    assert call_args.args[2] == 0.5  # pct
    assert "TP1" in call_args.args[3]  # reason
    assert state.tp1_hit is True


@pytest.mark.asyncio
async def test_scalp_tp1_does_not_fire_below_3pct():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=2.9)
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_not_awaited()


@pytest.mark.asyncio
async def test_scalp_tp2_fires_at_5pct_after_tp1():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=5.0, tp1_hit=True)
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_awaited_once()
    call_args = mgr._execute_sell.call_args
    assert call_args.args[2] == 1.0  # pct — sell 100% of remaining
    assert "TP2" in call_args.args[3]


@pytest.mark.asyncio
async def test_scalp_tp2_does_not_fire_without_tp1():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=5.0, tp1_hit=False)
    await mgr._evaluate_scalp("ADDR1", state)
    # Should fire TP1, not TP2
    call_args = mgr._execute_sell.call_args
    assert "TP1" in call_args.args[3]


@pytest.mark.asyncio
async def test_scalp_hard_stop_at_2pt5pct():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=-2.5)
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_awaited_once()
    call_args = mgr._execute_sell.call_args
    assert call_args.args[2] == 1.0  # sell 100%
    assert "stop" in call_args.args[3].lower()


@pytest.mark.asyncio
async def test_scalp_stop_does_not_fire_above_threshold():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=-2.4)
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_not_awaited()


@pytest.mark.asyncio
async def test_scalp_time_stop_at_45min():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=0.0, minutes_open=46)
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_awaited_once()
    call_args = mgr._execute_sell.call_args
    assert "time" in call_args.args[3].lower()


@pytest.mark.asyncio
async def test_scalp_time_stop_does_not_fire_before_45min():
    mgr = make_mgr()
    state = make_state(pnl_pct_val=0.0, minutes_open=44)
    await mgr._evaluate_scalp("ADDR1", state)
    mgr._execute_sell.assert_not_awaited()


@pytest.mark.asyncio
async def test_scalp_stop_notifies_scalp_queue():
    mgr = make_mgr()
    scalp_queue = MagicMock()
    mgr.scalp_queue = scalp_queue
    state = make_state(pnl_pct_val=-2.5)
    await mgr._evaluate_scalp("ADDR1", state)
    scalp_queue.on_scalp_close.assert_called_once()
    call_args = scalp_queue.on_scalp_close.call_args
    assert call_args.args[0] == "ADDR1"
    assert call_args.args[1] == "stop_loss"


@pytest.mark.asyncio
async def test_scalp_tp_notifies_scalp_queue_on_tp2():
    mgr = make_mgr()
    scalp_queue = MagicMock()
    mgr.scalp_queue = scalp_queue
    state = make_state(pnl_pct_val=5.0, tp1_hit=True)
    await mgr._evaluate_scalp("ADDR1", state)
    scalp_queue.on_scalp_close.assert_called_once()
    call_args = scalp_queue.on_scalp_close.call_args
    assert call_args.args[1] == "scalp_tp2"
