import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from breakout.execution import BreakoutExecution
from breakout.scoring import Kline
from breakout.state import BreakoutState, BreakoutPosition
from breakout.capital import BreakoutCapitalManager


def _k(close, o=100.0, h=101.0, l=99.0, v=1500.0):
    return Kline(0, o, h, l, close, v, 0)


def _make_config():
    c = MagicMock()
    c.breakout_position_usd = 500.0
    c.breakout_tp_pct = 4.0
    c.breakout_tp_sell_pct = 0.50
    c.breakout_stop_pct = 3.0
    c.breakout_trail_pct = 2.0
    c.breakout_max_hold_hours = 4.0
    c.breakout_cooldown_minutes = 45.0
    c.breakout_paper_taker_fee = 0.006
    return c


@pytest.fixture
def execution(tmp_path):
    from breakout.database import BreakoutDB
    cfg = _make_config()
    state = BreakoutState()
    capital = BreakoutCapitalManager(total_capital=2000.0, max_concurrent=4)
    paper_fill = AsyncMock()
    paper_fill.simulate_buy = AsyncMock(return_value=MagicMock(
        price=100.0, qty=5.0, usd_cost=500.0, fee_usd=3.0))
    paper_fill.simulate_sell = AsyncMock(return_value=MagicMock(
        price=104.0, qty=2.5, usd_proceeds=258.44, fee_usd=1.56))
    db = BreakoutDB(str(tmp_path / "breakout.db"))
    data_client = AsyncMock()
    return BreakoutExecution(
        data_client=data_client,
        paper_fill=paper_fill,
        capital=capital,
        state=state,
        db=db,
        config=cfg,
    )


@pytest.mark.asyncio
async def test_enter_creates_position(execution):
    candle = _k(close=100.5)
    await execution.enter(
        symbol="BTCUSDT", candle=candle, score=8,
        breakdown={"volume": 3, "body": 2, "breakout_size": 2, "trend": 1, "structure": 0, "total": 8},
        resistance=100.0, reason="score=8 breakout",
    )
    assert "BTCUSDT" in execution.state.open_positions
    pos = execution.state.open_positions["BTCUSDT"]
    assert pos.entry_price == 100.0
    assert pos.qty == 5.0
    assert pos.tp_price == pytest.approx(104.0)
    assert pos.stop_price == pytest.approx(97.0)
    assert pos.score == 8
    assert execution.capital.deployed_usd() == 500.0
    assert len(execution.db.get_open_positions()) == 1


def test_can_open_true_when_capacity(execution):
    assert execution.can_open() is True


def test_can_open_false_when_no_capacity(execution):
    for i in range(4):
        execution.capital.reserve(f"COIN{i}", 500.0)
    assert execution.can_open() is False


def test_is_in_cooldown_false_by_default(execution):
    assert execution.is_in_cooldown("BTCUSDT") is False


def test_is_in_cooldown_true_after_set(execution):
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    execution.db.set_cooldown("BTCUSDT",
                              cooldown_until_ts=future,
                              last_loss_pnl_usd=-15.0,
                              last_loss_time=past)
    assert execution.is_in_cooldown("BTCUSDT") is True


def test_is_in_cooldown_expired(execution):
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    execution.db.set_cooldown("BTCUSDT",
                              cooldown_until_ts=past,
                              last_loss_pnl_usd=-15.0,
                              last_loss_time=past)
    assert execution.is_in_cooldown("BTCUSDT") is False


def _seed_position(execution, symbol="BTCUSDT", entry=100.0, qty=5.0, score=8,
                   resistance=99.5, tp_pct=4.0, stop_pct=3.0, entry_time=None):
    if entry_time is None:
        entry_time = datetime.now(timezone.utc).isoformat()
    pos = BreakoutPosition(
        symbol=symbol, entry_time=entry_time,
        entry_price=entry, qty=qty, cost_usd=500.0, score=score,
        resistance_level=resistance,
        tp_price=entry * (1 + tp_pct / 100),
        stop_price=entry * (1 - stop_pct / 100),
        entry_candle_volume=1000.0, peak_price=entry,
    )
    execution.state.open_positions[symbol] = pos
    execution.capital.reserve(symbol, 500.0)
    execution.db.insert_open_position(
        symbol=symbol, entry_time=pos.entry_time,
        entry_price=pos.entry_price, qty=pos.qty, cost_usd=pos.cost_usd,
        score=pos.score, score_breakdown="{}",
        resistance_level=pos.resistance_level,
        tp_price=pos.tp_price, stop_price=pos.stop_price,
        entry_candle_volume=pos.entry_candle_volume, peak_price=pos.peak_price,
    )
    return pos


@pytest.mark.asyncio
async def test_stop_fires_when_price_hits_stop(execution):
    pos = _seed_position(execution)
    execution.paper_fill.simulate_sell = AsyncMock(return_value=MagicMock(
        price=97.0, qty=5.0, usd_proceeds=5.0 * 97.0 * (1 - 0.006), fee_usd=5.0 * 97.0 * 0.006))
    await execution._manage_one(pos, current_price=96.0, recent_k15=[])
    assert "BTCUSDT" not in execution.state.open_positions
    closed = execution.db.get_closed_positions()
    assert closed[0]["reason_exit"] == "stop-loss"
    assert execution.is_in_cooldown("BTCUSDT") is True


@pytest.mark.asyncio
async def test_tp1_fires_sells_half_activates_trail(execution):
    pos = _seed_position(execution)
    execution.paper_fill.simulate_sell = AsyncMock(side_effect=lambda symbol, qty:
        MagicMock(price=104.0, qty=qty, usd_proceeds=qty*104.0*(1-0.006), fee_usd=qty*104.0*0.006))
    await execution._manage_one(pos, current_price=104.5, recent_k15=[])
    assert "BTCUSDT" in execution.state.open_positions
    updated = execution.state.open_positions["BTCUSDT"]
    assert updated.tp_hit is True
    assert updated.qty == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_trail_exits_after_tp1(execution):
    pos = _seed_position(execution)
    pos.tp_hit = True
    pos.peak_price = 108.0
    pos.qty = 2.5
    execution.state.open_positions["BTCUSDT"] = pos
    execution.db.update_open_position("BTCUSDT", tp_hit=1, peak_price=108.0, qty=2.5)
    execution.paper_fill.simulate_sell = AsyncMock(return_value=MagicMock(
        price=105.8, qty=2.5, usd_proceeds=2.5*105.8*(1-0.006), fee_usd=2.5*105.8*0.006))
    await execution._manage_one(pos, current_price=105.8, recent_k15=[])
    assert "BTCUSDT" not in execution.state.open_positions
    closed = execution.db.get_closed_positions()
    assert closed[0]["reason_exit"] == "trail"


@pytest.mark.asyncio
async def test_max_hold_exits_position(execution):
    past = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    pos = _seed_position(execution, entry_time=past, resistance=90.0)
    execution.paper_fill.simulate_sell = AsyncMock(return_value=MagicMock(
        price=101.0, qty=5.0, usd_proceeds=5.0*101.0*(1-0.006), fee_usd=5.0*101.0*0.006))
    await execution._manage_one(pos, current_price=101.0, recent_k15=[])
    assert "BTCUSDT" not in execution.state.open_positions
    closed = execution.db.get_closed_positions()
    assert closed[0]["reason_exit"] == "max-hold"


@pytest.mark.asyncio
async def test_winning_close_no_cooldown(execution):
    pos = _seed_position(execution)
    pos.tp_hit = True
    pos.qty = 2.5
    pos._partial_proceeds = 2.5 * 104.0 * (1 - 0.006)
    pos._partial_fees = 2.5 * 104.0 * 0.006
    execution.state.open_positions["BTCUSDT"] = pos
    execution.db.update_open_position("BTCUSDT", tp_hit=1, qty=2.5, peak_price=105.0)
    pos.peak_price = 105.0
    execution.paper_fill.simulate_sell = AsyncMock(return_value=MagicMock(
        price=102.0, qty=2.5, usd_proceeds=2.5*102.0*(1-0.006), fee_usd=2.5*102.0*0.006))
    await execution._manage_one(pos, current_price=102.0, recent_k15=[])
    assert "BTCUSDT" not in execution.state.open_positions
    assert execution.is_in_cooldown("BTCUSDT") is False


@pytest.mark.asyncio
async def test_breakout_failed_early_exit(execution):
    pos = _seed_position(execution, resistance=99.5)
    execution.paper_fill.simulate_sell = AsyncMock(return_value=MagicMock(
        price=99.0, qty=5.0, usd_proceeds=5.0*99.0*(1-0.006), fee_usd=5.0*99.0*0.006))
    await execution._manage_one(pos, current_price=99.0, recent_k15=[])
    assert "BTCUSDT" not in execution.state.open_positions
    closed = execution.db.get_closed_positions()
    assert closed[0]["reason_exit"] == "breakout-failed"


@pytest.mark.asyncio
async def test_enter_blocks_duplicate(execution):
    candle = _k(close=100.5)
    await execution.enter(
        symbol="BTCUSDT", candle=candle, score=8,
        breakdown={"volume": 3, "body": 2, "breakout_size": 2, "trend": 1, "structure": 0, "total": 8},
        resistance=100.0, reason="first",
    )
    await execution.enter(
        symbol="BTCUSDT", candle=candle, score=8,
        breakdown={"volume": 3, "body": 2, "breakout_size": 2, "trend": 1, "structure": 0, "total": 8},
        resistance=100.0, reason="second",
    )
    assert execution.capital.deployed_usd() == 500.0
    assert len(execution.db.get_open_positions()) == 1
