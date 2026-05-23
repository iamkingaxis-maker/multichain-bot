import pytest
from scripts.sp4_common import (
    BotMetrics, pair_buys_sells, compute_metrics, confidence_label,
)


def _trade(bot_id, type_, token, price=0.001, pnl=None, time="2026-05-23T10:00:00+00:00"):
    t = {
        "bot_id": bot_id, "type": type_, "token": token,
        "entry_price": price, "time": time,
    }
    if pnl is not None:
        t["pnl"] = pnl
    if type_ == "buy":
        t["amount_usd"] = 20.0
    return t


def test_pair_buys_sells_simple_match():
    trades = [
        _trade("b1", "buy",  "A", price=0.001),
        _trade("b1", "sell", "A", price=0.001, pnl=2.0),
    ]
    paired = pair_buys_sells(trades)
    assert len(paired) == 1
    p = paired[0]
    assert p.bot_id == "b1"
    assert p.token == "A"
    assert p.realized_pnl_usd == 2.0


def test_pair_buys_sells_multi_sell_per_buy():
    trades = [
        _trade("b1", "buy",  "A", price=0.001),
        _trade("b1", "sell", "A", price=0.001, pnl=1.5),
        _trade("b1", "sell", "A", price=0.001, pnl=0.5),
    ]
    paired = pair_buys_sells(trades)
    assert len(paired) == 1
    assert paired[0].realized_pnl_usd == 2.0


def test_pair_buys_sells_filters_by_bot_id():
    trades = [
        _trade("b1", "buy",  "A", price=0.001),
        _trade("b2", "buy",  "A", price=0.001),
        _trade("b1", "sell", "A", price=0.001, pnl=1.0),
        _trade("b2", "sell", "A", price=0.001, pnl=-0.5),
    ]
    paired = pair_buys_sells(trades)
    by_bot = {p.bot_id: p for p in paired}
    assert by_bot["b1"].realized_pnl_usd == 1.0
    assert by_bot["b2"].realized_pnl_usd == -0.5


def test_pair_buys_sells_skips_unpaired_buy():
    trades = [_trade("b1", "buy", "A", price=0.001)]
    paired = pair_buys_sells(trades)
    assert len(paired) == 0


def test_compute_metrics_basic():
    from scripts.sp4_common import PairedTrade
    pairs = [
        PairedTrade(bot_id="b1", token="A", entry_price=0.001,
                    size_usd=20.0, realized_pnl_usd=2.0, time="t1",
                    sells=[], buy_meta={}),
        PairedTrade(bot_id="b1", token="B", entry_price=0.001,
                    size_usd=20.0, realized_pnl_usd=-1.0, time="t2",
                    sells=[], buy_meta={}),
        PairedTrade(bot_id="b1", token="C", entry_price=0.001,
                    size_usd=20.0, realized_pnl_usd=3.0, time="t3",
                    sells=[], buy_meta={}),
    ]
    metrics = compute_metrics(pairs)
    assert metrics.bot_id == "b1"
    assert metrics.sample_n == 3
    assert metrics.total_pnl_usd == 4.0
    assert metrics.pnl_per_trade == pytest.approx(4.0 / 3, abs=0.001)
    assert metrics.win_rate == pytest.approx(2 / 3, abs=0.001)


def test_compute_metrics_empty_returns_zero_sample():
    metrics = compute_metrics([])
    assert metrics.sample_n == 0
    assert metrics.total_pnl_usd == 0.0
    assert metrics.pnl_per_trade is None
    assert metrics.win_rate is None


def test_confidence_label_thresholds():
    assert confidence_label(0) == "Very low (n<5)"
    assert confidence_label(4) == "Very low (n<5)"
    assert confidence_label(5) == "Low (n<20)"
    assert confidence_label(19) == "Low (n<20)"
    assert confidence_label(20) == "OK"
    assert confidence_label(100) == "OK"
