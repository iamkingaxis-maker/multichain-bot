# tests/test_pump_retrace_gate.py
"""Pump-retrace gate (2026-07-03 evening-bleed autopsy).

Blocks family dip entries on tokens still UP > +50% (PUMP_RETRACE_H6_MIN) on
the 6h window — that dip is the unwind of a fresh pump, not a capitulation.
Ground truth: TATE fired at pc_h6=+286, Goofreck +73 during the 07-02 evening
-150pp bleed; scrubbed realized BLOCK cohort negative both time halves.
Fail-OPEN on missing/garbage data (read-as-zero bug-class rule).
"""
import math

from core.bot_evaluator import pump_retrace_blocks


class TestBlocks:
    def test_tate_blocked(self):
        blocked, why = pump_retrace_blocks(285.75)
        assert blocked is True
        assert "286" in why

    def test_goofreck_blocked(self):
        assert pump_retrace_blocks(73.03)[0] is True

    def test_threshold_boundary(self):
        assert pump_retrace_blocks(50.0)[0] is False   # strictly greater
        assert pump_retrace_blocks(50.1)[0] is True

    def test_capitulation_passes(self):
        # tonight's real winners: down hard on the window
        assert pump_retrace_blocks(-26.23)[0] is False
        assert pump_retrace_blocks(-61.54)[0] is False

    def test_flat_passes(self):
        assert pump_retrace_blocks(0.0)[0] is False
        assert pump_retrace_blocks(12.5)[0] is False


class TestFailOpen:
    def test_none_passes(self):
        assert pump_retrace_blocks(None)[0] is False

    def test_nan_passes(self):
        assert pump_retrace_blocks(math.nan)[0] is False

    def test_bool_passes(self):
        # bool is not a measurement (read-as-zero bug-class rule)
        assert pump_retrace_blocks(True)[0] is False

    def test_string_garbage_passes(self):
        assert pump_retrace_blocks("pumped")[0] is False

    def test_numeric_string_is_coerced(self):
        # DexScreener sometimes returns numeric strings — a real measurement
        assert pump_retrace_blocks("286")[0] is True


class TestEnv:
    def test_env_threshold_override(self, monkeypatch):
        monkeypatch.setenv("PUMP_RETRACE_H6_MIN", "100")
        assert pump_retrace_blocks(80)[0] is False
        assert pump_retrace_blocks(120)[0] is True

    def test_env_garbage_falls_back(self, monkeypatch):
        monkeypatch.setenv("PUMP_RETRACE_H6_MIN", "lots")
        assert pump_retrace_blocks(60)[0] is True

    def test_explicit_arg_beats_env(self, monkeypatch):
        monkeypatch.setenv("PUMP_RETRACE_H6_MIN", "10")
        assert pump_retrace_blocks(30, h6_min=50)[0] is False


def test_rollback_watcher_monitors_gate():
    from core.gate_rollback import MONITORED_GATES
    assert "pump_retrace_gate" in MONITORED_GATES
