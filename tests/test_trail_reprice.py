"""trail_reprice_would_fire — post-TP1 trail on fresh samples (family re-mine)."""
from core.fast_watch import trail_reprice_would_fire as trf


def test_fires_when_confirm_ticks_below_line():
    # entry 1.0, peak +10, trail 2 -> line +8; last two samples at +7
    fires, pnl, peak, why = trf([1.10, 1.07, 1.07], 1.0, 10.0, 2.0, confirm_ticks=2)
    assert fires is True and abs(pnl - 7.0) < 1e-6 and "trail" in why


def test_single_wick_does_not_fire():
    fires, _, _, _ = trf([1.10, 1.10, 1.07], 1.0, 10.0, 2.0, confirm_ticks=2)
    assert fires is False  # newest-1 is above the line


def test_eff_peak_uses_fresh_buffer_high():
    # recorded peak stale at +5 but buffer saw +20 -> line is +18, not +3
    fires, pnl, peak, _ = trf([1.20, 1.10, 1.10], 1.0, 5.0, 2.0, confirm_ticks=2)
    assert abs(peak - 20.0) < 1e-9 and fires is True and abs(pnl - 10.0) < 1e-6


def test_above_line_no_fire():
    fires, pnl, _, _ = trf([1.10, 1.095, 1.09], 1.0, 10.0, 2.0, confirm_ticks=2)
    assert fires is False and pnl is not None


def test_bad_data_fail_safe():
    assert trf([], 1.0, 10.0, 2.0)[0] is False
    assert trf([1.1], 0.0, 10.0, 2.0)[0] is False
    assert trf([1.1, 1.1], 1.0, 10.0, 0.0)[0] is False   # trail<=0 invalid
    assert trf(None, "x", None, 2.0)[0] is False
