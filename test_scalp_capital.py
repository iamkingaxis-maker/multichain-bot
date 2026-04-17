# test_scalp_capital.py
import time
import pytest
from core.scalp_capital import ScalpCapitalManager


def make_mgr(**kw):
    return ScalpCapitalManager(**kw)


def test_has_capacity_initially_true():
    mgr = make_mgr()
    assert mgr.has_capacity() is True


def test_has_capacity_false_when_full():
    mgr = make_mgr(max_concurrent=2)
    mgr.record_open("AAA", 200.0)
    mgr.record_open("BBB", 200.0)
    assert mgr.has_capacity() is False


def test_has_capacity_restored_after_close():
    mgr = make_mgr(max_concurrent=1)
    mgr.record_open("AAA", 200.0)
    assert mgr.has_capacity() is False
    mgr.record_close("AAA", pnl_usd=5.0)
    assert mgr.has_capacity() is True


def test_daily_loss_limit_blocks_capacity():
    mgr = make_mgr(daily_loss_limit=400.0)
    mgr.record_open("AAA", 200.0)
    mgr.record_close("AAA", pnl_usd=-401.0)
    assert mgr.has_capacity() is False


def test_daily_loss_not_hit_on_smaller_loss():
    mgr = make_mgr(daily_loss_limit=400.0)
    mgr.record_open("AAA", 200.0)
    mgr.record_close("AAA", pnl_usd=-100.0)
    assert mgr.has_capacity() is True


def test_deployed_usd():
    mgr = make_mgr()
    mgr.record_open("AAA", 200.0)
    mgr.record_open("BBB", 200.0)
    assert mgr.deployed_usd() == 400.0


def test_available_usd():
    mgr = make_mgr(total_capital=2000.0)
    mgr.record_open("AAA", 200.0)
    assert mgr.available_usd() == 1800.0


def test_record_close_removes_from_open():
    mgr = make_mgr()
    mgr.record_open("AAA", 200.0)
    mgr.record_close("AAA", pnl_usd=0.0)
    assert mgr.deployed_usd() == 0.0


def test_daily_loss_cumulative():
    mgr = make_mgr(daily_loss_limit=400.0)
    mgr.record_open("AAA", 200.0)
    mgr.record_close("AAA", pnl_usd=-200.0)
    mgr.record_open("BBB", 200.0)
    mgr.record_close("BBB", pnl_usd=-201.0)
    # cumulative -401 > 400 limit
    assert mgr.has_capacity() is False


def test_day_reset_clears_daily_loss():
    mgr = make_mgr(daily_loss_limit=400.0)
    mgr.record_open("AAA", 200.0)
    mgr.record_close("AAA", pnl_usd=-401.0)
    assert mgr.has_capacity() is False  # daily loss hit

    # Simulate day rollover by backdating the reset timestamp
    mgr._day_reset_ts = time.time() - 1  # pretend midnight already passed
    assert mgr.has_capacity() is True    # reset should have cleared the flag
