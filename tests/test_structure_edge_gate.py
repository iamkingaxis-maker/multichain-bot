"""Tests for the structure-edge entry gate (true-edge decomposition 2026-06-24).

Fire ONLY when pc_h6>=0 OR liquidity>=48k. BLOCK only a falling-knife (pc_h6<0)
in a thin book (liq<48k). Pure + fail-open.
"""
from core.bot_evaluator import structure_edge_blocks as seb


def test_blocks_falling_knife_thin_book():
    # pc_h6<0 AND liq<48k -> the -EV mass -> block
    b, why = seb(-12.0, 25000)
    assert b is True and "no structure edge" in why


def test_passes_reclaimed_h6():
    # pc_h6>=0 (reclaimed) even with thin liq -> pass (arm 1)
    assert seb(3.0, 20000)[0] is False
    assert seb(0.0, 20000)[0] is False  # boundary pc_h6==0 passes


def test_passes_deep_book():
    # deep liq even with negative h6 -> pass (arm 2)
    assert seb(-30.0, 60000)[0] is False
    assert seb(-30.0, 48000)[0] is False  # boundary liq==floor passes


def test_blocks_only_when_both_fail():
    assert seb(-1.0, 47999)[0] is True       # both fail by a hair -> block
    assert seb(-0.1, 48000)[0] is False      # liq saves it
    assert seb(0.0, 100)[0] is False         # h6 saves it


def test_fail_open_on_missing():
    # can't disprove the OR-of-passes if a feature is missing -> do NOT block
    assert seb(None, 20000)[0] is False
    assert seb(-12.0, None)[0] is False
    assert seb(None, None)[0] is False
    assert seb(float("nan"), 20000)[0] is False
    assert seb(-12.0, float("nan"))[0] is False


def test_floor_env_overridable(monkeypatch):
    monkeypatch.setenv("STRUCTURE_EDGE_LIQ_FLOOR", "30000")
    # at a 30k floor, liq=40k now passes (would've blocked at 48k default)
    assert seb(-5.0, 40000)[0] is False
    assert seb(-5.0, 29000)[0] is True


def test_explicit_floor_arg_wins(monkeypatch):
    monkeypatch.setenv("STRUCTURE_EDGE_LIQ_FLOOR", "30000")
    assert seb(-5.0, 35000, liq_floor=48000)[0] is True  # arg overrides env
