# tests/test_adaptive_entry.py
"""Adaptive entry levers (2026-07-07). swing_size_multiplier (token-conditional:
size down violent+shallow dead-cat tail) + vsnap_reject (fleet: reject fast
V-snaps, take grinds). Both FAIL-OPEN so a lane can never be starved to zero."""
from core.adaptive_entry import (swing_size_multiplier as ssm, vsnap_reject as vsr,
                                 update_recent_low as url)


class TestUpdateRecentLow:
    def test_seed_on_first_sample(self):
        low, restamp = url(None, 0.005)
        assert low == 0.005 and restamp is True     # first sample seeds + stamps

    def test_new_lower_low_restamps(self):
        low, restamp = url(0.005, 0.004)            # knifes lower
        assert low == 0.004 and restamp is True

    def test_above_low_ages(self):
        low, restamp = url(0.004, 0.006)            # recovers above the low
        assert low == 0.004 and restamp is False    # keep low, ts ages (grind)

    def test_equal_price_ages(self):
        low, restamp = url(0.004, 0.004)
        assert low == 0.004 and restamp is False

    def test_grind_sequence_low_stays_fixed(self):
        # a dip that bases and grinds up: low fixed after the bottom, ts ages
        low, seq = None, [0.010, 0.006, 0.004, 0.005, 0.007, 0.009]
        stamps = 0
        for p in seq:
            low, restamp = url(low, p)
            stamps += 1 if restamp else 0
        assert low == 0.004        # the bottom
        assert stamps == 3         # 0.010 seed, 0.006 new low, 0.004 new low; then ages

    def test_bad_price_keeps_low(self):
        low, restamp = url(0.004, "x")
        assert low == 0.004 and restamp is False


class TestSwingSizeMultiplier:
    def test_calm_full_size(self):
        assert ssm(20.0, -30.0) == 1.0        # pc_h24 < 80 -> not violent
        assert ssm(0.0, 0.0) == 1.0

    def test_violent_shallow_sizes_down_hard(self):
        # the DONALD #2 dead-cat: huge pump (h24 big) + shallow (h6 > -40)
        assert ssm(1448.0, 1448.0) == 0.45
        assert ssm(90.0, -10.0) == 0.45

    def test_violent_deep_keeps_most(self):
        # violent but a genuine deep dip -> keep EV
        assert ssm(1316.0, -45.0) == 0.70
        assert ssm(120.0, -60.0) == 0.70

    def test_boundary(self):
        assert ssm(80.0, -39.9) == 0.45       # exactly violent, shallow
        assert ssm(80.0, -40.0) == 0.70       # exactly violent, exactly deep
        assert ssm(79.9, -50.0) == 1.0        # just under violent -> full

    def test_missing_data_fails_open_full_size(self):
        assert ssm(None, -50.0) == 1.0        # no swing read -> never shrink
        assert ssm("x", -50.0) == 1.0
        assert ssm(float("nan"), -50.0) == 1.0

    def test_missing_h6_treated_not_deep(self):
        # violent + unknown depth -> assume shallow (conservative, size down)
        assert ssm(200.0, None) == 0.45


class TestVsnapReject:
    def test_fresh_snap_rejected(self):
        rej, why = vsr(120.0, 240.0)          # low 120s old < 240s threshold
        assert rej is True and "V-snap" in why

    def test_grind_allowed(self):
        rej, why = vsr(400.0, 240.0)          # low 400s old >= 240s -> grind
        assert rej is False and "grind" in why

    def test_unknown_age_fails_open(self):
        rej, why = vsr(None, 240.0)
        assert rej is False and "fail-open" in why

    def test_off_when_threshold_zero(self):
        assert vsr(1.0, 0.0)[0] is False
        assert vsr(1.0, -5.0)[0] is False

    def test_bad_age_fails_open(self):
        assert vsr("garbage", 240.0)[0] is False

    def test_boundary(self):
        assert vsr(240.0, 240.0)[0] is False   # exactly threshold -> allow (grind)
        assert vsr(239.9, 240.0)[0] is True
