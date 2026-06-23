"""Tests for the in-flight loss-floor exit (badday gap audit 2026-06-22).

Fires a full-close on a PRE-TP1 leg when intratrade pnl touches the MAE floor
(-7%) OR a never-green fast collapse (peak<2 AND pnl<=-4 AND drop_vel>=0.012).
Pure + fail-safe.
"""
from core.bot_evaluator import in_flight_floor_fires as iff


def test_mae_floor_fires_at_minus_7():
    # was-green leg (peak>=2 -> velbail skipped) so this isolates the pure MAE floor
    fires, why = iff(-7.0, 6.0, 300)
    assert fires is True and "MAE-floor" in why
    assert iff(-7.5, 6.0, 300)[0] is True
    # a never-green leg at -7 fires too (via the velocity pre-empt) — still a fire
    assert iff(-7.0, 1.0, 300)[0] is True


def test_above_floor_no_fire():
    # -6.5 is above the -7 floor; peak 1.0 (never green) but slow -> no velbail
    assert iff(-6.5, 1.0, 6000)[0] is False


def test_no_fire_when_healthy():
    # green winner dipping modestly -> never fire
    assert iff(-3.0, 8.0, 200)[0] is False


def test_velocity_bail_fast_never_green():
    # peak<2, pnl=-5 (>-7 floor), dropped 7pp in 100s = 0.07pp/s >= 0.012 -> velbail
    fires, why = iff(-5.0, 2.0 - 0.01, 100)
    assert fires is True and "velocity-bail" in why


def test_velocity_bail_not_when_slow():
    # same -5 but dropped 7pp over 2000s = 0.0035pp/s < 0.012, and -5 > -7 floor -> no fire
    assert iff(-5.0, 1.99, 2000)[0] is False


def test_velocity_bail_skipped_when_was_green():
    # peak 5 (was green, >= 2.0 velbail_peak_max) so velocity path skipped; -5 > -7 floor -> no fire
    assert iff(-5.0, 5.0, 50)[0] is False


def test_green_then_deep_still_floored():
    # even a was-green leg gets the -7 MAE floor (velbail skipped, floor still applies)
    assert iff(-8.0, 6.0, 50)[0] is True


def test_fail_safe_on_garbage():
    assert iff(None, 1.0, 100)[0] is False
    assert iff(-9.0, None, 100)[0] is False
    assert iff(float("nan"), 1.0, 100)[0] is False
    assert iff(-9.0, float("nan"), 100)[0] is False


def test_thresholds_overridable():
    # looser -8 floor: -7.5 no longer fires (and not a velbail: was-green)
    assert iff(-7.5, 6.0, 100, floor_pct=-8.0)[0] is False
    assert iff(-8.0, 6.0, 100, floor_pct=-8.0)[0] is True
