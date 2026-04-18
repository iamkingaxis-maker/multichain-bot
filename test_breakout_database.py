import pytest
from breakout.database import BreakoutDB


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "breakout_test.db")
    return BreakoutDB(path)


def test_schema_creates_all_tables(db):
    tables = db.list_tables()
    assert "breakout_positions" in tables
    assert "breakout_closed_positions" in tables
    assert "breakout_cooldowns" in tables


def test_insert_and_get_open_position(db):
    pos_id = db.insert_open_position(
        symbol="BTCUSDT",
        entry_time="2026-04-17T12:00:00Z",
        entry_price=100.0,
        qty=5.0,
        cost_usd=500.0,
        score=8,
        score_breakdown='{"volume":3,"body":2}',
        resistance_level=99.5,
        tp_price=104.0,
        stop_price=97.0,
        entry_candle_volume=1234.0,
        peak_price=100.0,
    )
    assert pos_id > 0
    rows = db.get_open_positions()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["score"] == 8
    assert rows[0]["tp_hit"] == 0


def test_duplicate_symbol_rejected(db):
    db.insert_open_position(
        symbol="BTCUSDT", entry_time="2026-04-17T12:00:00Z",
        entry_price=100.0, qty=5.0, cost_usd=500.0, score=8, score_breakdown="{}",
        resistance_level=99.5, tp_price=104.0, stop_price=97.0,
        entry_candle_volume=1234.0, peak_price=100.0,
    )
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_open_position(
            symbol="BTCUSDT", entry_time="2026-04-17T12:00:00Z",
            entry_price=101.0, qty=5.0, cost_usd=500.0, score=8, score_breakdown="{}",
            resistance_level=99.5, tp_price=104.0, stop_price=97.0,
            entry_candle_volume=1234.0, peak_price=100.0,
        )


def test_update_open_position(db):
    db.insert_open_position(
        symbol="BTCUSDT", entry_time="2026-04-17T12:00:00Z",
        entry_price=100.0, qty=5.0, cost_usd=500.0, score=8, score_breakdown="{}",
        resistance_level=99.5, tp_price=104.0, stop_price=97.0,
        entry_candle_volume=1234.0, peak_price=100.0,
    )
    db.update_open_position("BTCUSDT", peak_price=105.0, tp_hit=1)
    row = db.get_open_positions()[0]
    assert row["peak_price"] == 105.0
    assert row["tp_hit"] == 1


def test_close_position_moves_to_closed_table(db):
    db.insert_open_position(
        symbol="BTCUSDT", entry_time="2026-04-17T12:00:00Z",
        entry_price=100.0, qty=5.0, cost_usd=500.0, score=8, score_breakdown="{}",
        resistance_level=99.5, tp_price=104.0, stop_price=97.0,
        entry_candle_volume=1234.0, peak_price=100.0,
    )
    db.close_position(
        symbol="BTCUSDT",
        exit_time="2026-04-17T14:00:00Z",
        exit_price=104.0,
        proceeds_usd=520.0,
        pnl_usd=20.0,
        pnl_pct=4.0,
        reason_entry="score=8 breakout",
        reason_exit="tp1",
        fee_total_usd=3.0,
    )
    assert db.get_open_positions() == []
    closed = db.get_closed_positions()
    assert len(closed) == 1
    assert closed[0]["pnl_usd"] == 20.0
    assert closed[0]["reason_exit"] == "tp1"


def test_cooldown_set_and_query(db):
    db.set_cooldown("BTCUSDT", cooldown_until_ts="2026-04-17T15:00:00Z",
                    last_loss_pnl_usd=-15.0, last_loss_time="2026-04-17T14:00:00Z")
    assert db.is_in_cooldown("BTCUSDT", now_ts="2026-04-17T14:30:00Z") is True
    assert db.is_in_cooldown("BTCUSDT", now_ts="2026-04-17T15:30:00Z") is False


def test_cooldown_overwrites_previous(db):
    db.set_cooldown("BTCUSDT", "2026-04-17T14:00:00Z", -10.0, "2026-04-17T13:15:00Z")
    db.set_cooldown("BTCUSDT", "2026-04-17T16:00:00Z", -20.0, "2026-04-17T15:15:00Z")
    row = db.get_cooldown("BTCUSDT")
    assert row["cooldown_until_ts"] == "2026-04-17T16:00:00Z"
