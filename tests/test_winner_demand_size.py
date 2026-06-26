"""Tests for the winner size-up selector (winner mine 2026-06-25).

POSITIVE selector: median_buy_size_usd >= 34.3 = big buyers stepping in (the +EV
runner tail). Fire-on / size-up bias, never a hard gate. Pure + FAIL-OPEN.
"""
from core.bot_evaluator import winner_demand_selected as wds


def test_selected_at_and_above_threshold():
    assert wds(34.3)[0] is True and "median_buy_size" in wds(34.3)[1]
    assert wds(100.0)[0] is True


def test_not_selected_below_threshold():
    assert wds(34.2)[0] is False
    assert wds(0.0)[0] is False
    assert wds(10.0)[0] is False


def test_fail_open_on_missing():
    assert wds(None)[0] is False
    assert wds(float("nan"))[0] is False
    assert wds("garbage")[0] is False


def test_accepts_floatish():
    assert wds("50")[0] is True
    assert wds(50.0)[0] is True


def test_threshold_env_overridable(monkeypatch):
    monkeypatch.setenv("WINNER_SIZE_MEDIAN_BUY_USD", "100")
    assert wds(50.0)[0] is False     # 50 < 100 now
    assert wds(120.0)[0] is True


def test_explicit_threshold_arg_wins(monkeypatch):
    monkeypatch.setenv("WINNER_SIZE_MEDIAN_BUY_USD", "100")
    assert wds(50.0, threshold=34.3)[0] is True   # arg overrides env
    assert wds(30.0, threshold=34.3)[0] is False
