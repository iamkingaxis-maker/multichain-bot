import json
import pytest
from pathlib import Path
from core.multi_bot_persistence import MultiBotTradeStore


def test_record_trade_stamps_bot_id(tmp_path):
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.record_trade({
        "type": "buy", "token": "SQUIRE", "entry_price": 0.001,
        "amount_usd": 20.0, "time": "2026-05-23T10:00:00+00:00",
    }, bot_id="baseline_v1")
    trades_file = tmp_path / "trades.json"
    assert trades_file.exists()
    data = json.loads(trades_file.read_text())
    assert len(data) == 1
    assert data[0]["bot_id"] == "baseline_v1"
    assert data[0]["token"] == "SQUIRE"


def test_load_trades_filters_by_bot_id(tmp_path):
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.record_trade({"type": "buy", "token": "A", "time": "t1"}, bot_id="b1")
    store.record_trade({"type": "buy", "token": "B", "time": "t2"}, bot_id="b2")
    store.record_trade({"type": "buy", "token": "C", "time": "t3"}, bot_id="b1")
    b1_trades = store.load_trades(bot_id="b1")
    assert len(b1_trades) == 2
    assert {t["token"] for t in b1_trades} == {"A", "C"}
    b2_trades = store.load_trades(bot_id="b2")
    assert len(b2_trades) == 1
    assert b2_trades[0]["token"] == "B"


def test_load_trades_no_filter_returns_all(tmp_path):
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.record_trade({"type": "buy", "token": "A", "time": "t1"}, bot_id="b1")
    store.record_trade({"type": "buy", "token": "B", "time": "t2"}, bot_id="b2")
    assert len(store.load_trades()) == 2


def test_load_trades_backfills_baseline_v1_for_legacy_records(tmp_path):
    legacy = [
        {"type": "buy", "token": "OLD", "time": "t0"},
        {"type": "sell", "token": "OLD", "time": "t0.5", "pnl": 1.0},
    ]
    (tmp_path / "trades.json").write_text(json.dumps(legacy))
    store = MultiBotTradeStore(data_dir=tmp_path)
    trades = store.load_trades()
    assert all(t["bot_id"] == "baseline_v1" for t in trades)


def test_bot_state_save_load_roundtrip(tmp_path):
    from core.per_bot_capital import PerBotCapital
    store = MultiBotTradeStore(data_dir=tmp_path)
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=2000.0)
    cap.reserve_for_buy(20.0)
    store.save_bot_state("b1", cap.to_dict())
    loaded = store.load_bot_state("b1")
    assert loaded["balance_usd"] == 1980.0
    assert loaded["in_flight_usd"] == 20.0


def test_load_bot_state_returns_None_when_missing(tmp_path):
    store = MultiBotTradeStore(data_dir=tmp_path)
    assert store.load_bot_state("nonexistent") is None
