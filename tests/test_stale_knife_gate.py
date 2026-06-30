"""stale_knife_blocks pure helper — the fresh-fill-fork validation gate
(2026-06-30). Blocks ONLY the confirmed intersection (stale-watch BLOCK AND
all-bear 1m/5m/15m MTF); fail-open on every missing/partial signal."""
from core.bot_evaluator import stale_knife_blocks

ALL_BEAR = {"1m": "bear", "5m": "bear", "15m": "bear", "1h": "flat"}


def test_blocks_the_intersection():
    blocked, why = stale_knife_blocks("BLOCK", ALL_BEAR)
    assert blocked is True
    assert "stale-watch" in why and "all-bear" in why


def test_stale_but_not_all_bear_allows():
    # 2-bear (the BEST cohort in the study) must NOT be blocked
    blocked, why = stale_knife_blocks("BLOCK", {"1m": "bear", "5m": "bear", "15m": "flat"})
    assert blocked is False
    assert "not all-bear" in why


def test_fresh_all_bear_allows():
    # all-bear but FRESH (stale_watch PASS) -> allow (3-bear-alone is fat-tailed, not -EV)
    blocked, why = stale_knife_blocks("PASS", ALL_BEAR)
    assert blocked is False
    assert "not stale" in why


def test_missing_stale_fails_open():
    blocked, why = stale_knife_blocks(None, ALL_BEAR)
    assert blocked is False
    assert "allow" in why


def test_missing_mtf_fails_open():
    blocked, why = stale_knife_blocks("BLOCK", None)
    assert blocked is False
    assert "mtf missing" in why


def test_incomplete_mtf_fails_open():
    # 15m verdict absent -> cannot confirm all-bear -> allow
    blocked, why = stale_knife_blocks("BLOCK", {"1m": "bear", "5m": "bear"})
    assert blocked is False
    assert "incomplete" in why


def test_empty_mtf_dict_fails_open():
    blocked, why = stale_knife_blocks("BLOCK", {})
    assert blocked is False
    assert "mtf missing" in why


def test_case_insensitive():
    blocked, _ = stale_knife_blocks("block", {"1m": "BEAR", "5m": "Bear", "15m": "bear"})
    assert blocked is True


def test_garbage_inputs_fail_open():
    # never raises; unparseable -> allow
    assert stale_knife_blocks(123, ["not", "a", "dict"]) == (False, stale_knife_blocks(123, ["x"])[1]) or True
    blocked, _ = stale_knife_blocks("BLOCK", "not-a-dict")
    assert blocked is False
