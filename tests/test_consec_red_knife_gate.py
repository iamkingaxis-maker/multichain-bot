"""Tests for the no-bounce-knife entry gate (bounce-vs-knife study 2026-06-25).

Block a badday dip entry buying into N+ consecutive red 1m candles (still
falling = no-bounce knife). Pure + FAIL-OPEN on missing/NaN.
"""
from core.bot_evaluator import consec_red_knife_blocks as crk


def test_blocks_at_threshold_and_above():
    assert crk(3)[0] is True and "1m_consec_red=3" in crk(3)[1]
    assert crk(5)[0] is True
    assert crk(10)[0] is True


def test_passes_below_threshold():
    assert crk(0)[0] is False
    assert crk(1)[0] is False
    assert crk(2)[0] is False  # boundary: 2 < 3 passes


def test_fail_open_on_missing():
    assert crk(None)[0] is False
    assert crk(float("nan"))[0] is False
    assert crk("garbage")[0] is False


def test_accepts_floatish_values():
    assert crk(3.0)[0] is True
    assert crk("3")[0] is True       # string-coercible
    assert crk(2.9)[0] is False


def test_threshold_env_overridable(monkeypatch):
    monkeypatch.setenv("CONSEC_RED_KNIFE_THRESHOLD", "4")
    assert crk(3)[0] is False        # 3 < 4 now passes
    assert crk(4)[0] is True


def test_explicit_threshold_arg_wins(monkeypatch):
    monkeypatch.setenv("CONSEC_RED_KNIFE_THRESHOLD", "4")
    assert crk(3, threshold=3)[0] is True   # arg overrides env
    assert crk(2, threshold=3)[0] is False
