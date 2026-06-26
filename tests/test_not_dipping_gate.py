"""Tests for the not-dipping / slow-bleeder entry gate (slow-bleeder mine 2026-06-25).

Block a badday dip entry when the token is NOT actually dipping (flat/green 30m
macro, above MA50, non-falling 30m slope, OR near its 1h high). Pure + FAIL-OPEN:
each term contributes only when present; all-missing -> no block.
RULE = (macro30>=-4.83 OR ma50_dist>=-5.463 OR slope30m>=-0.1816) OR (range>=0.198)
"""
from core.bot_evaluator import not_dipping_blocks as nd


def test_blocks_when_trend_not_falling():
    # macro30 well above floor -> not dipping -> block
    assert nd(0.0, -20.0, -1.0, 0.05)[0] is True
    # ma50_dist above floor -> block
    assert nd(-30.0, 0.0, -1.0, 0.05)[0] is True
    # slope not falling -> block
    assert nd(-30.0, -20.0, 0.0, 0.05)[0] is True


def test_blocks_when_near_1h_high():
    # all trend terms dipping, but sitting near 1h high -> block (range arm)
    assert nd(-30.0, -20.0, -1.0, 0.5)[0] is True


def test_passes_a_genuine_dip():
    # falling on every axis AND low in 1h range -> a real dip -> PASS
    b, why = nd(-30.0, -20.0, -1.0, 0.05)
    assert b is False and why == ""


def test_fail_open_all_missing():
    assert nd(None, None, None, None)[0] is False
    assert nd(float("nan"), float("nan"), float("nan"), float("nan"))[0] is False


def test_fail_open_partial_missing_only_present_terms_count():
    # only slope present and it's falling -> no block (missing terms don't fire)
    assert nd(None, None, -1.0, None)[0] is False
    # only range present and high -> block
    assert nd(None, None, None, 0.3)[0] is True


def test_boundaries():
    assert nd(-4.83, -20.0, -1.0, 0.05)[0] is True   # macro30 == floor -> block
    assert nd(-4.84, -20.0, -1.0, 0.05)[0] is False  # just below -> pass (with other terms dipping)
    assert nd(-30.0, -20.0, -1.0, 0.198)[0] is True  # range == ceil -> block
    assert nd(-30.0, -20.0, -1.0, 0.197)[0] is False


def test_env_overridable(monkeypatch):
    monkeypatch.setenv("NOT_DIPPING_RANGE_CEIL", "0.5")
    assert nd(-30.0, -20.0, -1.0, 0.3)[0] is False   # 0.3 < 0.5 now passes
    assert nd(-30.0, -20.0, -1.0, 0.6)[0] is True


def test_explicit_args_win(monkeypatch):
    monkeypatch.setenv("NOT_DIPPING_RANGE_CEIL", "0.5")
    assert nd(-30.0, -20.0, -1.0, 0.3, range_ceil=0.2)[0] is True  # arg overrides env
