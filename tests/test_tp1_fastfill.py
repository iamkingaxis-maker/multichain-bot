# tests/test_tp1_fastfill.py
"""TP1 fastfill (2026-07-05 bounced-but-we-lost replay).

The exit loop checks TP1 on scan-cadence prices while peaks happen on fresh
ones: 27 rounds peaked above the +6 line, never filled TP1, bled -84.6pp via
breakeven-lock (replay: +24pp) — ~260pp/week of fill mechanics. The helper
fires only when confirm_ticks newest fresh samples ALL sit at/above the line
(wick-guarded, a 75% sell must never trip on one glitch print).
"""
from core.fast_watch import tp1_fastfill_would_fire


def _prices(entry, pnls):
    return [entry * (1 + p / 100.0) for p in pnls]


class TestFires:
    def test_confirmed_touch_fires(self):
        # FABLE class: fresh samples ride above +6 while the slow sweep misses it
        fires, pnl, why = tp1_fastfill_would_fire(
            _prices(1.0, [2, 4, 6.3, 6.7]), 1.0, 6.0)
        assert fires and pnl > 6 and "TP1 fastfill" in why

    def test_single_wick_does_not_fire(self):
        # one glitch print above the line, next sample back below -> no sell
        fires, _, _ = tp1_fastfill_would_fire(
            _prices(1.0, [2, 4, 8.0, 5.0]), 1.0, 6.0)
        assert not fires

    def test_two_ticks_required_by_default(self):
        fires, _, _ = tp1_fastfill_would_fire(
            _prices(1.0, [2, 4, 5.9, 6.1]), 1.0, 6.0)
        assert not fires  # only newest tick above the line

    def test_confirm_ticks_override(self):
        fires, _, _ = tp1_fastfill_would_fire(
            _prices(1.0, [2, 4, 5.9, 6.1]), 1.0, 6.0, confirm_ticks=1)
        assert fires

    def test_exact_line_counts(self):
        fires, _, _ = tp1_fastfill_would_fire(
            _prices(1.0, [5, 6.0, 6.0]), 1.0, 6.0)
        assert fires


class TestFailSafe:
    def test_bad_entry_price(self):
        assert tp1_fastfill_would_fire([1.0, 1.1], 0, 6.0)[0] is False
        assert tp1_fastfill_would_fire([1.0, 1.1], None, 6.0)[0] is False

    def test_bad_tp1(self):
        assert tp1_fastfill_would_fire([1.0, 1.1], 1.0, 0)[0] is False
        assert tp1_fastfill_would_fire([1.0, 1.1], 1.0, None)[0] is False

    def test_thin_or_garbage_samples(self):
        assert tp1_fastfill_would_fire([], 1.0, 6.0)[0] is False
        assert tp1_fastfill_would_fire(None, 1.0, 6.0)[0] is False
        assert tp1_fastfill_would_fire([1.07], 1.0, 6.0)[0] is False  # < confirm_ticks
        assert tp1_fastfill_would_fire(["x", -1, 0], 1.0, 6.0)[0] is False
