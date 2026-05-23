import pytest
from scripts.sp5_validate_champion import (
    check_sample_size, check_pnl_delta, check_drawdown,
    check_throughput, check_holdout, validate_all,
)
from scripts.sp4_common import BotMetrics


def _m(bot_id, n, per_tr, worst=-3.0):
    return BotMetrics(
        bot_id=bot_id, sample_n=n, total_pnl_usd=n * per_tr,
        pnl_per_trade=per_tr, win_rate=0.5, avg_win_usd=2.0,
        avg_loss_usd=-1.5, best_trade_usd=10.0, worst_trade_usd=worst,
        throughput_x_pnl=n * per_tr,
    )


def test_sample_size_pass():
    ok, msg = check_sample_size(_m("c", 30, 0.5), _m("b", 30, 0.3))
    assert ok is True


def test_sample_size_fail_when_champion_thin():
    ok, msg = check_sample_size(_m("c", 29, 0.5), _m("b", 30, 0.3))
    assert ok is False
    assert "champion" in msg.lower()


def test_sample_size_fail_when_baseline_thin():
    ok, msg = check_sample_size(_m("c", 30, 0.5), _m("b", 29, 0.3))
    assert ok is False
    assert "baseline" in msg.lower()


def test_pnl_delta_pass_when_at_least_10c_better():
    ok, msg = check_pnl_delta(_m("c", 30, 0.40), _m("b", 30, 0.30))
    assert ok is True


def test_pnl_delta_fail_when_under_10c_better():
    ok, msg = check_pnl_delta(_m("c", 30, 0.35), _m("b", 30, 0.30))
    assert ok is False


def test_drawdown_pass_when_not_materially_worse():
    ok, msg = check_drawdown(_m("c", 30, 0.5, worst=-3.5), _m("b", 30, 0.3, worst=-3.0))
    assert ok is True


def test_drawdown_fail_when_too_much_worse():
    ok, msg = check_drawdown(_m("c", 30, 0.5, worst=-4.0), _m("b", 30, 0.3, worst=-3.0))
    assert ok is False


def test_throughput_pass_when_champion_fires_enough():
    ok, msg = check_throughput(_m("c", 25, 0.5), _m("b", 30, 0.3))
    assert ok is True


def test_throughput_fail_when_champion_fires_too_few():
    ok, msg = check_throughput(_m("c", 14, 0.5), _m("b", 30, 0.3))
    assert ok is False


def test_holdout_pass_when_later_30pct_positive():
    from scripts.sp4_common import PairedTrade
    pairs = []
    for i in range(10):
        pairs.append(PairedTrade(
            bot_id="c", token=f"T{i}", entry_price=0.001, size_usd=20.0,
            realized_pnl_usd=1.0,
            time=f"2026-05-{i+1:02d}T00:00:00+00:00",
            sells=[], buy_meta={},
        ))
    ok, msg = check_holdout(pairs)
    assert ok is True


def test_holdout_fail_when_later_30pct_negative():
    from scripts.sp4_common import PairedTrade
    pairs = []
    for i in range(7):
        pairs.append(PairedTrade(
            bot_id="c", token=f"T{i}", entry_price=0.001, size_usd=20.0,
            realized_pnl_usd=2.0,
            time=f"2026-05-{i+1:02d}T00:00:00+00:00",
            sells=[], buy_meta={},
        ))
    for i in range(7, 10):
        pairs.append(PairedTrade(
            bot_id="c", token=f"T{i}", entry_price=0.001, size_usd=20.0,
            realized_pnl_usd=-3.0,
            time=f"2026-05-{i+1:02d}T00:00:00+00:00",
            sells=[], buy_meta={},
        ))
    ok, msg = check_holdout(pairs)
    assert ok is False


def test_validate_all_pass_when_every_gate_passes():
    ok, report = validate_all(
        champion_metrics=_m("c", 30, 0.5, worst=-3.0),
        baseline_metrics=_m("b", 30, 0.3, worst=-3.0),
        champion_pairs=[],
    )
    assert ok is True
    assert "PASS" in report


def test_validate_all_fails_when_any_gate_fails():
    ok, report = validate_all(
        champion_metrics=_m("c", 29, 0.5, worst=-3.0),
        baseline_metrics=_m("b", 30, 0.3, worst=-3.0),
        champion_pairs=[],
    )
    assert ok is False
    assert "FAIL" in report
