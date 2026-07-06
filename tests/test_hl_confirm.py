# tests/test_hl_confirm.py — confirmed higher-low entry trigger v2 (bucketed)
"""v1's tick-level 'no new low for 120s' was unreachable in production (any
dust wick reset the clock; 48/49 real entries stamped TRACKING). v2 ports the
trough study's exact winning cell: bucketed (60s) higher-low + 0.5% bounce."""
from core.fast_watch import hl_confirm_update as up, hl_confirm_state as state


def _feed(prices_times, bucket=60.0):
    st = {}
    for p, t in prices_times:
        up(st, p, t, bucket_secs=bucket)
    return st


def test_confirms_on_bucket_higher_low_and_bounce():
    # bucket 0: low 1.00; bucket 1: low 1.005 (higher) + last 1.01 (>= low*1.005)
    st = _feed([(1.05, 5), (1.00, 30), (1.005, 65), (1.01, 100)])
    assert state(st, 101.0) == "CONFIRMED"


def test_dust_wick_in_same_bucket_does_not_block_confirm():
    # v1 failure mode: micro new low mid-flush then real stabilization
    st = _feed([(1.00, 10), (0.99, 50),          # bucket 0 low .99
                (0.995, 70), (1.0, 100),          # bucket 1 low .995 > .99
                ])
    assert state(st, 101.0) == "CONFIRMED"


def test_new_bucket_lower_low_stays_tracking():
    st = _feed([(1.00, 10), (0.95, 70), (0.96, 100)])   # bucket1 low < bucket0
    assert state(st, 101.0) == "TRACKING"


def test_needs_bounce_off_true_low():
    # higher bucket low but price sitting ON the true low -> not confirmed
    st = _feed([(1.00, 10), (1.001, 70), (1.0005, 100)])
    assert state(st, 101.0, bounce_frac=0.005) == "TRACKING"


def test_single_bucket_never_confirms():
    st = _feed([(1.00, 10), (1.02, 30)])
    assert state(st, 31.0) == "TRACKING"


def test_expiry_and_stale():
    st = _feed([(1.00, 10), (1.05, 70)])
    assert state(st, 1900.0, expiry_secs=1800) == "EXPIRED"
    st2 = _feed([(1.00, 10), (1.05, 70)])
    assert state(st2, 150.0, stale_secs=30) == "STALE"


def test_garbage_price_ignored():
    st = _feed([(1.00, 10), ("x", 20), (None, 30), (-5, 40), (float("nan"), 50), (1.02, 65)])
    assert st["low"] == 1.00 and st["last"] == 1.02


def test_empty_state_is_tracking():
    assert state({}, 100.0) == "TRACKING"
    assert state(None, 100.0) == "TRACKING"
