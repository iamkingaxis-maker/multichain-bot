import json
import pytest
from unittest.mock import MagicMock
from breakout.state import BreakoutState, BreakoutPosition
from breakout.capital import BreakoutCapitalManager


@pytest.fixture
def dashboard_with_breakout(tmp_path):
    from dashboard.web_dashboard import WebDashboard
    from breakout.database import BreakoutDB

    tracker = MagicMock()
    dash = WebDashboard(tracker=tracker, port=8080)
    state = BreakoutState()
    capital = BreakoutCapitalManager()
    db = BreakoutDB(str(tmp_path / "breakout.db"))
    dash.register_breakout(state=state, capital=capital, db=db)
    return dash, state, capital, db


@pytest.mark.asyncio
async def test_api_breakout_state_returns_stats(dashboard_with_breakout):
    dash, state, capital, db = dashboard_with_breakout
    resp = await dash._handle_breakout_state(MagicMock())
    data = json.loads(resp.text)
    assert data["total_capital"] == 2000.0
    assert data["available"] == 2000.0
    assert data["deployed"] == 0.0
    assert data["open_count"] == 0


@pytest.mark.asyncio
async def test_api_breakout_watchlist(dashboard_with_breakout):
    dash, state, capital, db = dashboard_with_breakout
    state.set_watchlist(["BTCUSDT", "ETHUSDT"])
    resp = await dash._handle_breakout_watchlist(MagicMock())
    assert json.loads(resp.text) == ["BTCUSDT", "ETHUSDT"]


@pytest.mark.asyncio
async def test_api_breakout_positions_empty(dashboard_with_breakout):
    dash, *_ = dashboard_with_breakout
    resp = await dash._handle_breakout_positions(MagicMock())
    assert json.loads(resp.text) == []


@pytest.mark.asyncio
async def test_api_breakout_positions_returns_open(dashboard_with_breakout):
    dash, state, capital, db = dashboard_with_breakout
    pos = BreakoutPosition(
        symbol="BTCUSDT", entry_time="2026-04-17T12:00:00+00:00",
        entry_price=100.0, qty=5.0, cost_usd=500.0, score=8,
        resistance_level=99.5, tp_price=104.0, stop_price=97.0,
        entry_candle_volume=1000.0, peak_price=103.0, tp_hit=False,
    )
    state.open_positions["BTCUSDT"] = pos
    resp = await dash._handle_breakout_positions(MagicMock())
    data = json.loads(resp.text)
    assert len(data) == 1
    assert data[0]["symbol"] == "BTCUSDT"
    assert data[0]["score"] == 8
    assert data[0]["peak_price"] == 103.0


@pytest.mark.asyncio
async def test_api_breakout_closed_positions(dashboard_with_breakout):
    dash, state, capital, db = dashboard_with_breakout
    db.insert_open_position(
        symbol="BTCUSDT", entry_time="2026-04-17T12:00:00+00:00",
        entry_price=100.0, qty=5.0, cost_usd=500.0, score=8, score_breakdown="{}",
        resistance_level=99.5, tp_price=104.0, stop_price=97.0,
        entry_candle_volume=1000.0, peak_price=100.0,
    )
    db.close_position(
        symbol="BTCUSDT", exit_time="2026-04-17T13:00:00+00:00",
        exit_price=104.0, proceeds_usd=520.0, pnl_usd=20.0, pnl_pct=4.0,
        reason_entry="score=8", reason_exit="tp1", fee_total_usd=3.0,
    )
    req = MagicMock()
    req.query = {}
    resp = await dash._handle_breakout_closed(req)
    data = json.loads(resp.text)
    assert len(data) == 1
    assert data[0]["reason_exit"] == "tp1"
    assert data[0]["pnl_usd"] == 20.0
