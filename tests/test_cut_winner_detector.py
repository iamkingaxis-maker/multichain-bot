"""Tests for the PURE logic of scripts/cut_winner_detector.py.

These cover the two network-free functions:
  - is_cut_winner(exit_price, post_exit_highs, recovery_pct)
  - classify_exit(reason)
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.cut_winner_detector import classify_exit, is_cut_winner


# ── is_cut_winner ────────────────────────────────────────────────────────────

def test_recovers_above_threshold_is_cut_winner():
    # exit at 1.0; later high of 1.20 = +20% recovery, peak at minute 5.
    highs = [(1, 1.02), (2, 1.05), (5, 1.20), (10, 1.10)]
    cut, peak_pct, mins = is_cut_winner(1.0, highs, recovery_pct=15.0)
    assert cut is True
    assert peak_pct == 20.0
    assert mins == 5


def test_exactly_at_threshold_is_cut_winner():
    highs = [(3, 1.15)]
    cut, peak_pct, mins = is_cut_winner(1.0, highs, recovery_pct=15.0)
    assert cut is True
    assert peak_pct == 15.0
    assert mins == 3


def test_flat_is_not_cut_winner():
    highs = [(1, 1.0), (2, 1.0), (3, 1.0)]
    cut, peak_pct, mins = is_cut_winner(1.0, highs, recovery_pct=15.0)
    assert cut is False
    assert peak_pct == 0.0
    # peak is still reported (first/best high), but below threshold
    assert mins == 1


def test_down_only_is_not_cut_winner():
    highs = [(1, 0.95), (2, 0.90), (3, 0.80)]
    cut, peak_pct, mins = is_cut_winner(1.0, highs, recovery_pct=15.0)
    assert cut is False
    # best high is 0.95 -> -5% recovery
    assert round(peak_pct, 4) == -5.0
    assert mins == 1


def test_below_threshold_recovery_is_not_cut_winner():
    highs = [(1, 1.05), (2, 1.10), (4, 1.08)]
    cut, peak_pct, mins = is_cut_winner(1.0, highs, recovery_pct=15.0)
    assert cut is False
    assert round(peak_pct, 4) == 10.0
    assert mins == 2  # peak high (1.10) was at minute 2


def test_exit_price_zero_is_not_cut_winner():
    cut, peak_pct, mins = is_cut_winner(0.0, [(1, 5.0)], recovery_pct=15.0)
    assert cut is False
    assert peak_pct == 0.0
    assert mins is None


def test_exit_price_negative_is_not_cut_winner():
    cut, peak_pct, mins = is_cut_winner(-0.5, [(1, 5.0)], recovery_pct=15.0)
    assert cut is False
    assert peak_pct == 0.0
    assert mins is None


def test_empty_highs_is_not_cut_winner():
    cut, peak_pct, mins = is_cut_winner(1.0, [], recovery_pct=15.0)
    assert cut is False
    assert peak_pct == 0.0
    assert mins is None


# ── classify_exit ────────────────────────────────────────────────────────────

def test_classify_never_runner_is_stop():
    assert classify_exit("never_runner peak=0.00%<3.0 pnl=-13.61% hold=1min (floor)") == "stop"


def test_classify_hard_stop_is_stop():
    assert classify_exit("hard stop pnl=-15.93% <= -15.0") == "stop"


def test_classify_giveback_floor_is_stop():
    assert classify_exit("giveback floor pnl=-20.04% after peak +8.5% (gap-through guard)") == "stop"


def test_classify_fast_dump_bail_is_stop():
    assert classify_exit("fast-dump bail pnl=-15.05% <= -15.0 (any volume, gap-through guard)") == "stop"


def test_classify_time_box_is_stop():
    # time-box forced exit (closes the position by clock, not a profit target)
    assert classify_exit("time-box exit 6min (pnl=-3.22%)") == "stop"


def test_classify_tp1_is_tp():
    assert classify_exit("TP1 pnl=23.27% >= 20.0") == "tp"


def test_classify_tp2_is_tp():
    assert classify_exit("TP2 pnl=12.93% >= 12.0") == "tp"


def test_classify_trail_is_tp():
    # trailing-stop exits realize profit off a peak -> tp-family, not a hard cut
    assert classify_exit("trail pnl=2.31% <= peak(9.47%) - 2.0pp") == "tp"


def test_classify_unknown_is_other():
    assert classify_exit("cancelled on restart") == "other"
    assert classify_exit("") == "other"
    assert classify_exit(None) == "other"
