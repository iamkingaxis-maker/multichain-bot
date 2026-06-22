"""filter_terminal_collapse — pc_h6 floor catching death-spiral corpses (2026-06-22).

Data-validated separator from the live QAI -$55 loss: the corpse entered at
pc_h6=-85.3%, the next-deepest live trade was -46.3%, and every winner was
>= -32% pc_h6. A pc_h6 <= -60% floor catches the corpse class with a 25-point
margin and clips zero winners in the live set. Must fail-OPEN on missing data
(never block when pc_h6 is absent/garbage) so it can't silently kill volume.
"""
import pytest

from core.bot_evaluator import terminal_collapse_blocks


def test_qai_corpse_blocked():
    blocked, why = terminal_collapse_blocks(-85.3)
    assert blocked and "pc_h6" in why


def test_threshold_boundary_inclusive():
    assert terminal_collapse_blocks(-60.0)[0] is True   # <= threshold blocks
    assert terminal_collapse_blocks(-59.9)[0] is False  # just above passes


def test_next_deepest_trade_passes():
    # ATTENTION -46.3% (a small loser) must NOT be clipped at the -60 floor
    assert terminal_collapse_blocks(-46.3)[0] is False


def test_worst_winner_passes():
    # leverageIT winner entered at pc_h6 -32.4%
    assert terminal_collapse_blocks(-32.4)[0] is False


def test_positive_h6_passes():
    assert terminal_collapse_blocks(74.0)[0] is False


def test_fail_open_on_missing():
    assert terminal_collapse_blocks(None)[0] is False
    assert terminal_collapse_blocks("n/a")[0] is False


def test_custom_threshold_env(monkeypatch):
    monkeypatch.setenv("TERMINAL_COLLAPSE_H6_PCT", "-50")
    assert terminal_collapse_blocks(-55.0)[0] is True   # below -50 now blocks
    assert terminal_collapse_blocks(-46.3)[0] is False  # still above -50


def test_explicit_threshold_arg_overrides():
    assert terminal_collapse_blocks(-70.0, threshold=-80.0)[0] is False
    assert terminal_collapse_blocks(-85.0, threshold=-80.0)[0] is True
