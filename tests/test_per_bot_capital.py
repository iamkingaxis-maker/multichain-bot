import pytest
from core.per_bot_capital import PerBotCapital


def test_capital_init_starting_balance():
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=2000.0)
    assert cap.bot_id == "b1"
    assert cap.balance_usd == 2000.0
    assert cap.in_flight_usd == 0.0
    assert cap.realized_pnl_total_usd == 0.0
    assert cap.daily_pnl_usd == 0.0


def test_capital_reserve_reduces_balance():
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=2000.0)
    cap.reserve_for_buy(20.0)
    assert cap.balance_usd == 1980.0
    assert cap.in_flight_usd == 20.0


def test_capital_reserve_rejects_when_insufficient():
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=20.0)
    with pytest.raises(ValueError, match="insufficient"):
        cap.reserve_for_buy(30.0)


def test_capital_realize_sell_adds_proceeds():
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=2000.0)
    cap.reserve_for_buy(20.0)
    cap.realize_sell(cost_usd=20.0, proceeds_usd=23.0)
    assert cap.balance_usd == 2003.0
    assert cap.in_flight_usd == 0.0
    assert cap.realized_pnl_total_usd == 3.0
    assert cap.daily_pnl_usd == 3.0


def test_capital_daily_reset_at_utc_midnight_rollover():
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=2000.0)
    cap.reserve_for_buy(20.0)
    cap.realize_sell(cost_usd=20.0, proceeds_usd=22.0, now_iso="2026-05-22T23:59:59Z")
    assert cap.daily_pnl_usd == 2.0
    # First action on the next UTC day resets daily P&L
    cap.reserve_for_buy(20.0)
    cap.realize_sell(cost_usd=20.0, proceeds_usd=19.0, now_iso="2026-05-23T00:00:01Z")
    assert cap.daily_pnl_usd == -1.0
    assert cap.realized_pnl_total_usd == 1.0  # cumulative still adds up


def test_capital_to_dict_from_dict_roundtrip():
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=2000.0)
    cap.reserve_for_buy(20.0)
    d = cap.to_dict()
    restored = PerBotCapital.from_dict(d)
    assert restored.bot_id == "b1"
    assert restored.balance_usd == 1980.0
    assert restored.in_flight_usd == 20.0
