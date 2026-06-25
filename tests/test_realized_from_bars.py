"""Tests for realized_from_bars — the forward-candle realized-P&L model.

BUG (2026-06-25): the model checked window min_low <= -7 FIRST, so any forward
path that EVER wicked -7% was scored as a -7 stop, even if it pumped +50% first.
On volatile microcaps (wick >7% routinely) that collapsed ~every entry to -7,
giving zero discrimination between blocked/passed cohorts. Fix: walk bars
CHRONOLOGICALLY and score whichever threshold (TP +5 / stop -7) is hit FIRST.
"""
from collections import namedtuple

from scripts.audit_filter_shadow_log import realized_from_bars

Bar = namedtuple("Bar", "open_time open high low close")


def _bars(block_close, forward_hilo):
    """block bar at t=0 (close=block_close), then forward bars at t=1.. with
    (high_pct, low_pct) relative to block_close (and close=high for simplicity)."""
    out = [Bar(0, block_close, block_close, block_close, block_close)]
    for i, (hi_pct, lo_pct) in enumerate(forward_hilo, start=1):
        hi = block_close * (1 + hi_pct / 100)
        lo = block_close * (1 + lo_pct / 100)
        out.append(Bar(i, lo, hi, lo, hi))  # close at the high (end-on-up)
    return out


def test_pump_then_dip_scores_tp_not_stop():
    # bar1 pumps +10% (TP), bar2 wicks -10%. OLD model -> -7 (min_low). FIXED -> TP.
    bars = _bars(100.0, [(10, 1), (0, -10)])
    n, mg, ml, end, realized = realized_from_bars(bars, block_ts=0)
    assert ml <= -7              # the window DID wick past -7 (the old trap)
    assert realized > 0          # but TP was hit FIRST -> positive, not -7
    assert realized != -7.0


def test_dip_then_pump_scores_stop():
    # bar1 wicks -10% (stop) FIRST, then bar2 pumps +10%. -> stopped at -7.
    bars = _bars(100.0, [(0, -10), (10, 1)])
    _, _, _, _, realized = realized_from_bars(bars, block_ts=0)
    assert realized == -7.0


def test_neither_threshold_scores_end():
    # stays within (-7, +5): realized = end_pct, floored at -7.
    bars = _bars(100.0, [(2, -1), (3, -2)])
    _, _, _, end, realized = realized_from_bars(bars, block_ts=0)
    assert realized == max(end, -7.0)
    assert -7.0 < realized < 5.0


def test_within_bar_both_breached_is_conservative_stop():
    # a single bar that wicks BOTH -10 and +10: assume stop first (conservative).
    bars = _bars(100.0, [(10, -10)])
    _, _, _, _, realized = realized_from_bars(bars, block_ts=0)
    assert realized == -7.0


def test_empty_or_no_forward_returns_none():
    assert realized_from_bars([], block_ts=0) is None
    only_block = [Bar(0, 100, 100, 100, 100)]
    assert realized_from_bars(only_block, block_ts=0) is None  # no forward bars


def test_discrimination_restored_across_a_cohort():
    # A mix: a clean runner vs a knife. They must NOT both score -7 (the bug).
    runner = realized_from_bars(_bars(100.0, [(20, 2), (5, -8)]), 0)[4]   # TP first
    knife = realized_from_bars(_bars(100.0, [(1, -12)]), 0)[4]            # stop first
    assert runner != knife
    assert runner > 0 and knife == -7.0
