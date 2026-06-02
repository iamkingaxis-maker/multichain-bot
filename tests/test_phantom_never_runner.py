"""Phantom-parity for the never-runner exit (scripts/live_forward_test.simulate_phantom_never_runner).
Mirrors core/per_bot_position_manager tick: peak<peak_max gate (winner-safe) + floor/timebox arms."""
from scripts.live_forward_test import simulate_phantom_never_runner

MIN_MS = 60_000


def _oldest_first_to_input(candles_oldest):
    # the sim takes newest-first and reverses internally
    return list(reversed(candles_oldest))


def _c(minute, o, h, l, c):
    return [minute * MIN_MS, o, h, l, c, 0]


def test_phantom_floor_arm_fires_at_loss_floor():
    # entry 1.0; never green; low hits -7% at minute 20 -> floor arm exits at -6
    candles = [_c(0, 1.0, 1.01, 0.99, 1.0),
               _c(10, 0.99, 1.0, 0.97, 0.98),
               _c(20, 0.98, 0.99, 0.93, 0.94)]  # low -7%
    r = simulate_phantom_never_runner(1.0, _oldest_first_to_input(candles))
    assert r['fired'] is True and r['exit_reason'] == 'nr_floor'
    assert r['phantom_pnl_pct'] == -6.0


def test_phantom_timebox_arm_fires_after_minutes():
    # flat near -1%, never green, never hits -6%; at minute 50 (>=45) timebox exits at close
    candles = [_c(0, 1.0, 1.005, 0.99, 0.995),
               _c(30, 0.99, 1.0, 0.985, 0.99),
               _c(50, 0.99, 1.0, 0.985, 0.99)]
    r = simulate_phantom_never_runner(1.0, _oldest_first_to_input(candles))
    assert r['fired'] is True and r['exit_reason'] == 'nr_timebox'


def test_phantom_winner_safe_when_high_crosses_peak_max():
    # high crosses +4% early (peak>=3) -> never-runner disabled even though it later dumps -8%
    candles = [_c(0, 1.0, 1.04, 1.0, 1.03),    # high +4% -> peak gate trips
               _c(20, 1.03, 1.03, 0.92, 0.93)]  # later dump to -7/-8%
    r = simulate_phantom_never_runner(1.0, _oldest_first_to_input(candles))
    assert r['fired'] is False  # trail-safe: runner cohort never clipped


def test_phantom_no_ohlcv():
    r = simulate_phantom_never_runner(1.0, [])
    assert r['fired'] is False and r['exit_reason'] == 'no_ohlcv'
