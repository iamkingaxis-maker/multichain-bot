"""Tests for the falling-day-flush entry gate (loss-tail decomposition 2026-06-22).

Block = DOWN on the day (pc_h24<0) AND extreme h1 flush (pc_h1<=-35). The pc_h24
sign is the state-switch: a deep flush is a buyable pullback on an UP day, a
structural collapse on a DOWN day. Pure + fail-open.
"""
import os
import pytest
from core.bot_evaluator import falling_day_flush_blocks as fdf


def test_blocks_dying_token_freefall():
    # down on the day AND extreme flush -> block
    blocked, why = fdf(-50.0, -40.0)
    assert blocked is True
    assert "pc_h24" in why and "pc_h1" in why


def test_keeps_pullback_in_uptrend():
    # the winner profile: UP on the day (+90%), moderate flush -> never block
    blocked, _ = fdf(90.0, -25.0)
    assert blocked is False


def test_keeps_deep_flush_when_up_on_day():
    # extreme flush but UP on the day -> NOT blocked (pc_h24 sign is the switch)
    blocked, _ = fdf(20.0, -45.0)
    assert blocked is False


def test_keeps_down_day_but_shallow_flush():
    # down on the day but flush not extreme (-25 > -35) -> NOT blocked
    blocked, _ = fdf(-30.0, -25.0)
    assert blocked is False


def test_boundary_h1_exactly_threshold_blocks():
    # pc_h1 == -35 is at/below the ceiling -> block (<=)
    assert fdf(-10.0, -35.0)[0] is True
    # pc_h1 == -34.9 just above -> keep
    assert fdf(-10.0, -34.9)[0] is False


def test_boundary_h24_exactly_zero_keeps():
    # pc_h24 == 0 is NOT < 0 -> keep (strict)
    assert fdf(0.0, -40.0)[0] is False
    assert fdf(-0.1, -40.0)[0] is True


def test_fail_open_on_missing():
    assert fdf(None, -40.0)[0] is False
    assert fdf(-50.0, None)[0] is False
    assert fdf("x", -40.0)[0] is False
    assert fdf(float("nan"), -40.0)[0] is False
    assert fdf(-50.0, float("nan"))[0] is False


def test_env_overridable_h1_max(monkeypatch):
    monkeypatch.setenv("FALLING_DAY_FLUSH_H1_MAX", "-50.0")
    # -40 no longer extreme enough under -50 ceiling
    assert fdf(-10.0, -40.0)[0] is False
    assert fdf(-10.0, -50.0)[0] is True


def test_explicit_thresholds_override_env(monkeypatch):
    monkeypatch.setenv("FALLING_DAY_FLUSH_H1_MAX", "-50.0")
    # explicit h1_max arg wins over env
    assert fdf(-10.0, -36.0, h1_max=-35.0)[0] is True
