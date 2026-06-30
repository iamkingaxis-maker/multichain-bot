"""dev_not_dumped_blocks pure helper (MAE selection mine 2026-06-30). Block ONLY a
confirmed dev-dump (dev_pct_remaining present AND < min_pct); fail-open on missing."""
from core.bot_evaluator import dev_not_dumped_blocks


def test_blocks_confirmed_dump():
    blocked, why = dev_not_dumped_blocks(5.0, min_pct=20)
    assert blocked is True
    assert "dev_pct_remaining=5" in why and "dev dumped" in why


def test_allows_dev_holding():
    blocked, why = dev_not_dumped_blocks(40.0, min_pct=20)
    assert blocked is False
    assert "holds" in why


def test_boundary_at_threshold_allows():
    # >= min_pct passes (only strictly below blocks)
    blocked, _ = dev_not_dumped_blocks(20.0, min_pct=20)
    assert blocked is False


def test_missing_fails_open():
    assert dev_not_dumped_blocks(None, min_pct=20)[0] is False
    assert "missing" in dev_not_dumped_blocks(None, min_pct=20)[1]


def test_nan_fails_open():
    blocked, _ = dev_not_dumped_blocks(float("nan"), min_pct=20)
    assert blocked is False


def test_bool_treated_as_missing_not_number():
    # bool is a subclass of int — must NOT be read as 0/1 dev_pct
    blocked, why = dev_not_dumped_blocks(True, min_pct=20)
    assert blocked is False


def test_garbage_fails_open():
    blocked, _ = dev_not_dumped_blocks("not-a-number", min_pct=20)
    assert blocked is False


def test_default_threshold_is_20():
    # env-default path (no min_pct passed): 19 blocks, 20 allows
    assert dev_not_dumped_blocks(19.0)[0] is True
    assert dev_not_dumped_blocks(20.0)[0] is False
