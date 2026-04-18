import pytest
from unittest.mock import AsyncMock, MagicMock
from breakout.execution import BreakoutExecution
from breakout.scoring import Kline
from breakout.state import BreakoutState
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
