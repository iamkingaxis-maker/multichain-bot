"""Winner-selection entry gate for the patient sleeve (2026-06-26).

Gate ON => ALLOW only winner-selected entries (median_buy_size_usd >= 34.3).
FAIL-CLOSED: missing signal while gated => BLOCK (hold only qualified +tail entries).
Gate OFF => never blocks (every other bot is unaffected).
"""
from core.bot_evaluator import winner_select_entry_blocks as wseb


def test_no_block_when_gate_off():
    assert wseb(10.0, gate_on=False)[0] is False
    assert wseb(None, gate_on=False)[0] is False


def test_block_when_gated_and_not_selected():
    assert wseb(10.0, gate_on=True)[0] is True


def test_pass_when_gated_and_selected():
    assert wseb(50.0, gate_on=True)[0] is False


def test_fail_closed_on_missing_signal():
    assert wseb(None, gate_on=True)[0] is True
    assert wseb(float("nan"), gate_on=True)[0] is True
    assert wseb("garbage", gate_on=True)[0] is True


def test_threshold_override():
    assert wseb(50.0, gate_on=True, threshold=100.0)[0] is True   # 50 < 100
    assert wseb(120.0, gate_on=True, threshold=100.0)[0] is False
