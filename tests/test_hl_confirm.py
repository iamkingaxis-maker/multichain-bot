# tests/test_hl_confirm.py — confirmed higher-low entry trigger (trough anatomy)
from core.fast_watch import hl_confirm_update as up, hl_confirm_state as state


def _feed(prices_times):
    st = {}
    for p, t in prices_times:
        up(st, p, t)
    return st


def test_confirms_after_hold_and_bounce():
    # low at t=0 (1.00), then flat 1.02 for 160s -> confirmed at t=160
    st = _feed([(1.05, -10), (1.00, 0), (1.02, 30), (1.02, 155)])
    assert state(st, 160.0, hold_secs=150) == "CONFIRMED"


def test_new_low_resets_hold():
    st = _feed([(1.00, 0), (1.02, 100), (0.98, 140), (0.99, 175)])
    # low moved at t=140 -> hold clock restarts; at t=200 only 60s since low
    assert state(st, 200.0, hold_secs=150, stale_secs=60) == "TRACKING"
    up(st, 0.991, 289)
    assert state(st, 289.0, hold_secs=150) == "TRACKING"   # hold not met (149s)
    up(st, 0.991, 291)
    assert state(st, 291.0, hold_secs=150) == "CONFIRMED"  # 151s + bounce +1.12%


def test_needs_bounce_not_just_time():
    # no new low for ages but price sits ON the low -> not confirmed
    st = _feed([(1.00, 0), (1.001, 295)])
    assert state(st, 300.0, hold_secs=150, bounce_frac=0.01) == "TRACKING"


def test_expiry_and_stale():
    st = _feed([(1.00, 0), (1.05, 100)])
    assert state(st, 1900.0, expiry_secs=1800) == "EXPIRED"
    st2 = _feed([(1.00, 0), (1.05, 100)])
    assert state(st2, 200.0, stale_secs=30) == "STALE"   # last sample 100s ago


def test_garbage_price_ignored():
    st = _feed([(1.00, 0), ("x", 10), (None, 20), (-5, 30), (float("nan"), 40), (1.02, 50)])
    assert st["low"] == 1.00 and st["last"] == 1.02


def test_empty_state_is_tracking():
    assert state({}, 100.0) == "TRACKING"
    assert state(None, 100.0) == "TRACKING"
