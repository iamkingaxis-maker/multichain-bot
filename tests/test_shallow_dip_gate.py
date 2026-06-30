"""shallow_dip_blocks pure helper (solve-it band analysis 2026-06-30). Block a dip
that is NOT deep enough (pc_h6 present AND > depth_max); fail-open on missing.
Deeper is BETTER here (opposite of a too-deep floor)."""
from core.bot_evaluator import shallow_dip_blocks


def test_blocks_shallow_dip():
    blocked, why = shallow_dip_blocks(-10.0, depth_max=-30)
    assert blocked is True
    assert "shallow" in why


def test_blocks_pump_retrace():
    blocked, _ = shallow_dip_blocks(5.0, depth_max=-30)
    assert blocked is True


def test_allows_deep_decline():
    blocked, why = shallow_dip_blocks(-45.0, depth_max=-30)
    assert blocked is False
    assert "deep decline" in why


def test_allows_crater():
    assert shallow_dip_blocks(-70.0, depth_max=-30)[0] is False


def test_boundary_at_depth_max_allows():
    # exactly at -30 (<=) passes; just above blocks
    assert shallow_dip_blocks(-30.0, depth_max=-30)[0] is False
    assert shallow_dip_blocks(-29.9, depth_max=-30)[0] is True


def test_missing_fails_open():
    assert shallow_dip_blocks(None, depth_max=-30)[0] is False
    assert "missing" in shallow_dip_blocks(None, depth_max=-30)[1]


def test_nan_and_bool_fail_open():
    assert shallow_dip_blocks(float("nan"), depth_max=-30)[0] is False
    assert shallow_dip_blocks(True, depth_max=-30)[0] is False  # bool != number


def test_garbage_fails_open():
    assert shallow_dip_blocks("x", depth_max=-30)[0] is False


def test_default_threshold_is_minus30():
    assert shallow_dip_blocks(-29.0)[0] is True   # shallow vs default -30
    assert shallow_dip_blocks(-31.0)[0] is False  # deep
