"""Tests for the profit-sweep shadow simulator (display-only banking math)."""
from core.profit_sweep_sim import (
    realized_curve, hwm_banked, step_banked, simulate_bot,
)


def test_realized_curve_tracks_peak_above_current():
    # +10 then give back to +6: current 6, peak 10
    cur, peak = realized_curve([4.0, 6.0, -4.0])
    assert cur == 6.0
    assert peak == 10.0


def test_hwm_banked_is_fraction_of_peak():
    # peak 10, give-back to 6 → HWM-50 banks 5 (locked at the peak, not 3)
    _, peak = realized_curve([4.0, 6.0, -4.0])
    assert hwm_banked(peak, 0.5) == 5.0
    assert hwm_banked(peak, 1.0) == 10.0


def test_hwm_never_negative():
    _, peak = realized_curve([-3.0, -2.0])  # never green
    assert peak == 0.0
    assert hwm_banked(peak, 0.5) == 0.0


def test_step_quantizes_to_increments():
    # peak 12, $5 step, bank 50% per step → floor(12/5)=2 steps → 0.5*5*2 = 5
    assert step_banked(12.0, 5.0, 0.5) == 5.0
    # below one step → banks nothing (the size-trigger never fires)
    assert step_banked(4.0, 5.0, 0.5) == 0.0


def test_step_zero_when_no_step_size():
    assert step_banked(100.0, 0.0) == 0.0


def test_simulate_bot_full_shape():
    # +10 peak, back to +6, step $5
    sim = simulate_bot([4.0, 6.0, -4.0], step_dollars=5.0)
    assert sim["realized_now"] == 6.0
    assert sim["realized_peak"] == 10.0
    assert sim["banked_hwm_50"] == 5.0
    assert sim["banked_hwm_100"] == 10.0
    assert sim["banked_step"] == 5.0  # floor(10/5)=2 → 0.5*5*2
    assert sim["at_risk_now"] == 1.0  # current 6 - banked 5


def test_simulate_bot_loser_banks_nothing():
    sim = simulate_bot([-2.0, -3.0], step_dollars=5.0)
    assert sim["banked_hwm_50"] == 0.0
    assert sim["banked_step"] == 0.0
    assert sim["at_risk_now"] == 0.0
