"""falling_knife entry-gate helper (2026-06-27 winner-mining cycle).

Block a badday dip entry only when BOTH: multi-timeframe trend bearish
(mtf<=-1) AND the latest 1m bar still red (last_close<0). Fail-open on any
missing/NaN feature (telemetry-gap safety)."""
import math

from core.bot_evaluator import falling_knife_blocks


def test_blocks_when_bearish_mtf_and_red_last_bar():
    block, why = falling_knife_blocks(mtf_score=-1.0, last_close_pct=-0.83)
    assert block is True
    assert "falling knife" in why


def test_blocks_deeper_bearish():
    block, _ = falling_knife_blocks(mtf_score=-3.0, last_close_pct=-2.0)
    assert block is True


def test_passes_when_last_bar_green():
    # mtf bearish but the most recent 1m closed GREEN -> confirmation -> allow
    block, why = falling_knife_blocks(mtf_score=-2.0, last_close_pct=0.08)
    assert block is False
    assert why == ""


def test_passes_when_mtf_not_bearish():
    # last bar red but trend not bearish (mtf=0) -> not a knife
    block, _ = falling_knife_blocks(mtf_score=0.0, last_close_pct=-1.0)
    assert block is False


def test_boundary_mtf_minus_one_is_bearish():
    assert falling_knife_blocks(-1.0, -0.01)[0] is True


def test_boundary_last_close_zero_is_not_red():
    # exactly 0.0 is NOT < 0 -> allow
    assert falling_knife_blocks(-2.0, 0.0)[0] is False


def test_fail_open_on_missing_mtf():
    assert falling_knife_blocks(None, -1.0) == (False, "")


def test_fail_open_on_missing_last_close():
    assert falling_knife_blocks(-2.0, None) == (False, "")


def test_fail_open_on_nan():
    assert falling_knife_blocks(float("nan"), -1.0) == (False, "")
    assert falling_knife_blocks(-2.0, float("nan")) == (False, "")


def test_fail_open_on_garbage():
    assert falling_knife_blocks("x", "y") == (False, "")


def test_custom_mtf_max():
    # with mtf_max=-2, an mtf of -1 no longer qualifies as bearish-enough
    assert falling_knife_blocks(-1.0, -1.0, mtf_max=-2.0)[0] is False
    assert falling_knife_blocks(-2.0, -1.0, mtf_max=-2.0)[0] is True
