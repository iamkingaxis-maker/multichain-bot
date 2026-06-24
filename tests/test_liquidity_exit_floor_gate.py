"""Tests for the liquidity-exit-floor entry gate (exit-tail design 2026-06-24).

Block a badday entry when the ENTRY liquidity is a finite number AND below the
floor (a book too thin to exit cleanly -> the gap-through tail). FAIL-OPEN on
None/NaN (can't disprove exitability -> don't block). Pure, never raises.
Mirrors tests/test_structure_edge_gate.py.
"""
from core.bot_evaluator import liquidity_exit_floor_blocks as lefb


def test_blocks_below_floor():
    b, why = lefb(20000, 30000)
    assert b is True and "below exit-floor" in why


def test_passes_at_or_above_floor():
    assert lefb(30000, 30000)[0] is False   # boundary: at floor passes
    assert lefb(48000, 30000)[0] is False
    assert lefb(1_000_000, 30000)[0] is False


def test_fail_open_on_missing():
    # can't disprove exitability if liquidity is absent -> do NOT block
    assert lefb(None, 30000)[0] is False
    assert lefb(float("nan"), 30000)[0] is False


def test_fail_open_on_garbage():
    assert lefb("notanumber", 30000)[0] is False
    assert lefb(float("inf"), 30000)[0] is False


def test_floor_env_overridable(monkeypatch):
    monkeypatch.setenv("LIQ_EXIT_FLOOR_USD", "50000")
    # at a 50k floor, 40k now blocks (would've passed at 30k default)
    assert lefb(40000)[0] is True
    assert lefb(60000)[0] is False


def test_default_floor_is_30000(monkeypatch):
    monkeypatch.delenv("LIQ_EXIT_FLOOR_USD", raising=False)
    assert lefb(29999)[0] is True
    assert lefb(30000)[0] is False


def test_explicit_floor_arg_wins(monkeypatch):
    monkeypatch.setenv("LIQ_EXIT_FLOOR_USD", "50000")
    # explicit arg overrides the env
    assert lefb(40000, floor_usd=30000)[0] is False
    assert lefb(25000, floor_usd=30000)[0] is True


def test_zero_and_negative_block():
    # a 0/near-0 book is unexitable -> block (finite and < floor)
    assert lefb(0, 30000)[0] is True
    assert lefb(-5, 30000)[0] is True
