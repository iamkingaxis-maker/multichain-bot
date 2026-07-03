# tests/test_young_holder_guard.py
"""Young-lane holder-concentration rug guard (2026-07-03).

Ground truth from the lane's first live-paper day (07-02): NEVER rugged -83%
in 113s with top1=44.86/top10=100.57 at entry; every winner sat at top1 20-24 /
top10 54-64 (or missing data). The guard blocks top1>=30 OR top10>=70 and MUST
fail-open on missing/garbage data (missing-data-read-as-zero bug-class rule).
"""
import math

from core.young_token_probe import (
    holder_guard_blocks,
    holder_guard_max_top1,
    holder_guard_max_top10,
    holder_guard_mode,
)


class TestHolderGuardBlocks:
    def test_never_rug_blocked(self):
        # NEVER at entry: top1=44.86 top10=100.57 -> -83% in 113s
        assert holder_guard_blocks(44.86, 100.57) is True

    def test_mensa_kevin_cotoro_rebuy_blocked(self):
        assert holder_guard_blocks(35.0, 87.16) is True    # MENSA -13.2
        assert holder_guard_blocks(65.0, 167.17) is True   # KEVIN -5.9
        assert holder_guard_blocks(43.09, 95.19) is True   # COTORO rebuy -37pp

    def test_winners_pass(self):
        assert holder_guard_blocks(23.52, 55.63) is False  # "1" +80
        assert holder_guard_blocks(22.07, 63.6) is False   # ELON +23
        assert holder_guard_blocks(22.17, 56.69) is False  # paging +10
        assert holder_guard_blocks(20.69, 60.95) is False  # Mymo (small loss, fair)

    def test_top1_alone_blocks(self):
        assert holder_guard_blocks(30.0, 10.0) is True
        assert holder_guard_blocks(29.9, 10.0) is False

    def test_top10_alone_blocks(self):
        assert holder_guard_blocks(5.0, 70.0) is True
        assert holder_guard_blocks(5.0, 69.9) is False


class TestFailOpen:
    """Missing-data-read-as-zero bug-class: absent/garbage data must PASS."""

    def test_both_none_passes(self):
        assert holder_guard_blocks(None, None) is False

    def test_one_none_other_clean_passes(self):
        assert holder_guard_blocks(None, 55.0) is False
        assert holder_guard_blocks(22.0, None) is False

    def test_one_none_other_dirty_still_blocks(self):
        # partial data that IS present and over the line must still block
        assert holder_guard_blocks(None, 95.0) is True
        assert holder_guard_blocks(44.0, None) is True

    def test_bool_is_not_a_measurement(self):
        assert holder_guard_blocks(True, True) is False

    def test_nan_passes(self):
        assert holder_guard_blocks(math.nan, math.nan) is False

    def test_string_garbage_passes(self):
        assert holder_guard_blocks("44", {}) is False


class TestEnvDefaults:
    def test_default_mode_enforce(self, monkeypatch):
        monkeypatch.delenv("YOUNG_HOLDER_GUARD_MODE", raising=False)
        assert holder_guard_mode() == "enforce"

    def test_mode_override(self, monkeypatch):
        monkeypatch.setenv("YOUNG_HOLDER_GUARD_MODE", "shadow")
        assert holder_guard_mode() == "shadow"

    def test_threshold_defaults(self, monkeypatch):
        monkeypatch.delenv("YOUNG_HOLDER_MAX_TOP1", raising=False)
        monkeypatch.delenv("YOUNG_HOLDER_MAX_TOP10", raising=False)
        assert holder_guard_max_top1() == 30.0
        assert holder_guard_max_top10() == 70.0

    def test_threshold_env_garbage_falls_back(self, monkeypatch):
        monkeypatch.setenv("YOUNG_HOLDER_MAX_TOP1", "not-a-number")
        assert holder_guard_max_top1() == 30.0

    def test_threshold_override_applies(self, monkeypatch):
        monkeypatch.setenv("YOUNG_HOLDER_MAX_TOP1", "50")
        # 44.86 passes at a 50 threshold (top10 still catches NEVER though)
        assert holder_guard_blocks(44.86, None, max_top1=None, max_top10=999) is False
