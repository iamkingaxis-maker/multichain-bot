# tests/test_blackout_rca.py — invariants from the 2026-07-05 BLACKOUT incident
"""Bug A: gate_rollback consumed the MIXED-cohort wr and latched working gates
off (structure_edge sat rolled-back for days while logs said BLOCK).
Bug B (latent): a signed-but-unverifiable swap returned None -> refund-after-
spend. These tests pin both fixes."""
import pathlib

from core.gate_rollback import evaluate_gate_rollback


class TestRollbackConsumesBlockedCohortOnly:
    def test_mixed_wr_alone_cannot_roll_back(self):
        # the old poison: winning PASSES inflate mixed wr; block_wr absent
        stats = {"block_n": 50, "wr": 90.0, "block_avg": 5.0}   # no block_wr
        rolled, why = evaluate_gate_rollback(stats)
        assert rolled is False
        assert "block_wr" in why

    def test_blocked_cohort_winning_rolls_back(self):
        stats = {"block_n": 50, "block_wr": 80.0, "block_avg": 5.0, "wr": 10.0}
        rolled, why = evaluate_gate_rollback(stats)
        assert rolled is True

    def test_blocked_cohort_losing_stays_enforcing(self):
        stats = {"block_n": 50, "block_wr": 20.0, "block_avg": -4.0, "wr": 95.0}
        rolled, _ = evaluate_gate_rollback(stats)
        assert rolled is False


def test_scorer_emits_block_wr():
    src = pathlib.Path("scripts/audit_filter_shadow_log.py").read_text(encoding="utf-8")
    assert '"block_wr": block_wr' in src


def test_rolled_back_gates_warn_loudly():
    # every rollback-guarded enforce gate must announce the latch instead of
    # printing a false BLOCK silently
    src = pathlib.Path("feeds/dip_scanner.py").read_text(encoding="utf-8")
    assert src.count("GATE-ROLLED-BACK") >= 9


def test_signed_unverifiable_swap_never_returns_none():
    # the refund-after-spend hole: signature + blind adoption => spent:True
    src = pathlib.Path("feeds/dip_scanner.py").read_text(encoding="utf-8")
    seg = src[src.find("unconfirmed_execute_adoption_blind"):]
    assert seg, "adoption-blind branch missing"
    assert '"spent": True' in seg[:1200]
    # and the branch triggers on signature-present + pre_bal blind
    idx = src.find('elif res.get("signature") and _pre_bal < 0:')
    assert idx > 0, "guard condition missing"


class TestLatchClear:
    def test_env_clear_resets_latch(self, tmp_path, monkeypatch):
        import importlib
        import core.gate_rollback as gr
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        importlib.reload(gr)
        gr.set_rollback("structure_edge", True, "test latch")
        assert gr.is_rolled_back("structure_edge") is True
        monkeypatch.setenv("GATE_ROLLBACK_CLEAR", "structure_edge")
        gr._cleared_once = False   # simulate fresh boot
        assert gr.is_rolled_back("structure_edge") is False
        # idempotent + only-once per process
        gr.set_rollback("structure_edge", True, "re-latch")
        assert gr.is_rolled_back("structure_edge") is True

    def test_no_env_no_clear(self, tmp_path, monkeypatch):
        import importlib
        import core.gate_rollback as gr
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        monkeypatch.delenv("GATE_ROLLBACK_CLEAR", raising=False)
        importlib.reload(gr)
        gr.set_rollback("x_gate", True, "latch")
        gr._cleared_once = False
        assert gr.is_rolled_back("x_gate") is True
