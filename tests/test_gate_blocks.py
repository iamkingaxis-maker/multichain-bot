"""gate_blocks: reusable MODE-flag arbiter (2026-06-27 filter-audit).

A filter actually blocks only when verdict==BLOCK AND mode resolves to enforce.
Default behavior-preserving (enforce) so adding a flag to a hard-enforced gate is
a no-op until explicitly demoted to shadow."""
from core.bot_evaluator import gate_blocks


def test_enforce_blocks_on_block_verdict():
    assert gate_blocks("BLOCK", "enforce") is True


def test_enforce_does_not_block_on_pass():
    assert gate_blocks("PASS", "enforce") is False


def test_shadow_never_blocks():
    assert gate_blocks("BLOCK", "shadow") is False
    assert gate_blocks("PASS", "shadow") is False


def test_off_never_blocks():
    assert gate_blocks("BLOCK", "off") is False


def test_case_and_whitespace_insensitive():
    assert gate_blocks("BLOCK", " Enforce ") is True
    assert gate_blocks("BLOCK", "SHADOW") is False


def test_default_is_behavior_preserving_enforce():
    # empty/None mode falls back to default 'enforce' (a pre-flag hard gate keeps blocking)
    assert gate_blocks("BLOCK", "") is True
    assert gate_blocks("BLOCK", None) is True


def test_default_override_to_shadow():
    # a gate that should default-loosen can pass default_mode='shadow'
    assert gate_blocks("BLOCK", "", default_mode="shadow") is False


def test_garbage_mode_falls_back_to_enforce_default():
    assert gate_blocks("BLOCK", "banana") is False  # unknown mode != enforce -> no block
