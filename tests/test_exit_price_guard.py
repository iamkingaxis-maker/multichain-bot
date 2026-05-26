"""Tests for the transient price-glitch exit guard.

Reference incidents (2026-05-26):
  TROLL — real ~$0.092, one tick read ~$0.021 (−77%), reverted next tick. GLITCH.
  PTAI  — real rug, ~$0.00237 → $4.28e-5 (−98%) and kept falling. REAL.
"""

import core.exit_price_guard as eg


def test_first_observation_seeds_and_returns():
    guard = {}
    assert eg.guarded_exit_price(guard, "X", 1.0) == 1.0
    assert guard["X"]["last_good"] == 1.0
    assert guard["X"]["pending"] is None


def test_normal_move_passes_through_and_updates_last_good():
    guard = {"X": {"last_good": 1.0, "pending": None}}
    # small downward move (−10%), well under max_drop → acted on immediately
    assert eg.guarded_exit_price(guard, "X", 0.90) == 0.90
    assert guard["X"]["last_good"] == 0.90


def test_ordinary_stop_size_move_not_deferred():
    # the −15% hard stop must still fire instantly: −20% < 40% drop → pass through
    guard = {"X": {"last_good": 1.0, "pending": None}}
    assert eg.guarded_exit_price(guard, "X", 0.80) == 0.80


def test_catastrophic_drop_deferred_first_cycle():
    # >40% drop in one cycle → suspect → act on last-good, stash pending
    guard = {"X": {"last_good": 1.0, "pending": None}}
    out = eg.guarded_exit_price(guard, "X", 0.23)   # −77%
    assert out == 1.0                                # NOT the glitch price
    assert guard["X"]["pending"] == 0.23
    assert guard["X"]["last_good"] == 1.0


def test_glitch_reverts_never_acts_on_bad_price():
    # TROLL: 0.092 → 0.021 (deferred) → 0.092 (recovery). Bad price never used.
    guard = {}
    eg.guarded_exit_price(guard, "TROLL", 0.092)
    assert eg.guarded_exit_price(guard, "TROLL", 0.021) == 0.092   # deferred
    assert eg.guarded_exit_price(guard, "TROLL", 0.092) == 0.092   # recovered
    assert guard["TROLL"]["pending"] is None
    assert guard["TROLL"]["last_good"] == 0.092


def test_real_crash_confirms_second_cycle():
    # PTAI: 0.00237 → 4.28e-5 (deferred) → lower (confirms) → stop can fire
    guard = {}
    eg.guarded_exit_price(guard, "PTAI", 0.00237)
    assert eg.guarded_exit_price(guard, "PTAI", 4.28e-5) == 0.00237   # deferred
    out = eg.guarded_exit_price(guard, "PTAI", 3.1e-5)               # corroborates
    assert out == 3.1e-5                                              # accepted → stop
    assert guard["PTAI"]["last_good"] == 3.1e-5
    assert guard["PTAI"]["pending"] is None


def test_sustained_low_confirms_even_if_slightly_higher_within_tol():
    # suspect low holds within +10% next cycle → confirmed
    guard = {}
    eg.guarded_exit_price(guard, "X", 1.0)
    assert eg.guarded_exit_price(guard, "X", 0.20) == 1.0   # deferred, pending=0.20
    # next read 0.21 is within 0.20*1.10=0.22 → confirm
    assert eg.guarded_exit_price(guard, "X", 0.21) == 0.21


def test_partial_recovery_still_suspect_redefers_not_confirm():
    # deferred at 0.20; next reads 0.40 which is still >40% down from 1.0 but
    # NOT within tol of the 0.20 pending → re-defer (don't confirm a moving low)
    guard = {}
    eg.guarded_exit_price(guard, "X", 1.0)
    assert eg.guarded_exit_price(guard, "X", 0.20) == 1.0    # pending=0.20
    assert eg.guarded_exit_price(guard, "X", 0.40) == 1.0    # 0.40 > 0.22 → re-defer
    assert guard["X"]["pending"] == 0.40
    # now 0.40 holds → confirm
    assert eg.guarded_exit_price(guard, "X", 0.40) == 0.40


def test_upward_spike_is_not_an_adverse_move():
    # a big UP move is not a drop → accepted (this guard only defers crashes)
    guard = {"X": {"last_good": 1.0, "pending": None}}
    assert eg.guarded_exit_price(guard, "X", 3.0) == 3.0
    assert guard["X"]["last_good"] == 3.0


def test_recovery_clears_stale_pending():
    guard = {}
    eg.guarded_exit_price(guard, "X", 1.0)
    eg.guarded_exit_price(guard, "X", 0.10)        # suspect, pending set
    assert guard["X"]["pending"] == 0.10
    eg.guarded_exit_price(guard, "X", 0.95)        # recovery clears pending
    assert guard["X"]["pending"] is None
    assert guard["X"]["last_good"] == 0.95
