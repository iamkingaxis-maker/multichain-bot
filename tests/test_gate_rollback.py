"""Tests for the forward-validation auto-rollback watcher (core/gate_rollback)."""
import json
import core.gate_rollback as gr


def test_rollback_when_blocked_cohort_winning():
    # blocked cohort forward-winning (majority win + positive mean, enough n) -> roll back
    # BLACKOUT RCA 2026-07-05: rollback consumes the BLOCKED cohort wr only
    s = {"block_n": 30, "block_wr": 62.0, "block_avg": 8.5}
    should, why = gr.evaluate_gate_rollback(s)
    assert should is True and "clipping winners" in why


def test_no_rollback_when_blocked_cohort_losing():
    # the GOOD case: blocked cohort forward-losing -> keep enforcing
    s = {"block_n": 40, "wr": 20.0, "block_avg": -22.0}
    assert gr.evaluate_gate_rollback(s)[0] is False


def test_no_rollback_when_thin():
    s = {"block_n": 5, "wr": 80.0, "block_avg": 30.0}
    should, why = gr.evaluate_gate_rollback(s)
    assert should is False and "thin" in why


def test_no_rollback_high_wr_but_negative_mean():
    # majority barely-green but mean negative (one big loser) -> NOT clipping winners
    assert gr.evaluate_gate_rollback({"block_n": 30, "wr": 55.0, "block_avg": -3.0})[0] is False


def test_no_rollback_positive_mean_but_minority_win():
    # one huge winner drags mean positive but most lose -> guard requires BOTH -> no rollback
    assert gr.evaluate_gate_rollback({"block_n": 30, "wr": 40.0, "block_avg": 2.0})[0] is False


def test_fail_safe_on_garbage():
    assert gr.evaluate_gate_rollback(None)[0] is False
    assert gr.evaluate_gate_rollback({})[0] is False
    assert gr.evaluate_gate_rollback({"block_n": 30, "wr": None, "block_avg": None})[0] is False


def test_state_roundtrip_and_failsafe(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # default: not rolled back
    assert gr.is_rolled_back("falling_day_flush") is False
    gr.set_rollback("falling_day_flush", True, "test reason", {"block_n": 30})
    assert gr.is_rolled_back("falling_day_flush") is True
    # unknown gate -> False
    assert gr.is_rolled_back("nonexistent") is False


def test_run_check_sticky_and_triggers(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    fwd = {
        "falling_day_flush": {"block_n": 30, "block_wr": 65.0, "block_avg": 9.0},   # winning -> roll back
        "solpump_neg_gate": {"block_n": 40, "wr": 15.0, "block_avg": -25.0},  # losing -> keep
    }
    res = dict((g, (rb, why)) for g, rb, why in gr.run_gate_rollback_check(fwd))
    assert res["falling_day_flush"][0] is True
    assert res["solpump_neg_gate"][0] is False
    assert gr.is_rolled_back("falling_day_flush") is True
    assert gr.is_rolled_back("solpump_neg_gate") is False
    # STICKY: even if falling_day_flush now looks losing, it stays rolled back
    fwd2 = {"falling_day_flush": {"block_n": 50, "wr": 10.0, "block_avg": -30.0}}
    gr.run_gate_rollback_check(fwd2, gates=["falling_day_flush"])
    assert gr.is_rolled_back("falling_day_flush") is True
