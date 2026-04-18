import pytest
from breakout.capital import BreakoutCapitalManager


def make_mgr(**kw):
    return BreakoutCapitalManager(**kw)


def test_has_capacity_initially_true():
    assert make_mgr().has_capacity(500.0) is True


def test_has_capacity_false_when_max_concurrent_reached():
    mgr = make_mgr(max_concurrent=2)
    mgr.reserve("BTCUSDT", 500.0)
    mgr.reserve("ETHUSDT", 500.0)
    assert mgr.has_capacity(500.0) is False


def test_has_capacity_false_when_insufficient_funds():
    mgr = make_mgr(total_capital=1000.0, max_concurrent=10)
    mgr.reserve("BTCUSDT", 500.0)
    mgr.reserve("ETHUSDT", 500.0)
    assert mgr.has_capacity(500.0) is False  # 0 available


def test_reserve_moves_from_available_to_deployed():
    mgr = make_mgr(total_capital=2000.0)
    mgr.reserve("BTCUSDT", 500.0)
    assert mgr.available_usd() == 1500.0
    assert mgr.deployed_usd() == 500.0


def test_release_returns_proceeds_and_accumulates_pnl():
    mgr = make_mgr(total_capital=2000.0)
    mgr.reserve("BTCUSDT", 500.0)
    mgr.release("BTCUSDT", proceeds_usd=520.0, cost_usd=500.0)
    assert mgr.available_usd() == 2020.0
    assert mgr.deployed_usd() == 0.0
    assert mgr.realized_pnl() == 20.0


def test_release_negative_pnl():
    mgr = make_mgr(total_capital=2000.0)
    mgr.reserve("BTCUSDT", 500.0)
    mgr.release("BTCUSDT", proceeds_usd=480.0, cost_usd=500.0)
    assert mgr.realized_pnl() == -20.0


def test_release_unknown_symbol_noop():
    mgr = make_mgr()
    mgr.release("NOPE", proceeds_usd=0.0, cost_usd=0.0)
    assert mgr.deployed_usd() == 0.0


def test_stats_dict():
    mgr = make_mgr(total_capital=2000.0, max_concurrent=4)
    mgr.reserve("BTCUSDT", 500.0)
    s = mgr.stats()
    assert s["total_capital"] == 2000.0
    assert s["available"] == 1500.0
    assert s["deployed"] == 500.0
    assert s["open_count"] == 1
    assert s["max_concurrent"] == 4
    assert s["realized_pnl"] == 0.0
