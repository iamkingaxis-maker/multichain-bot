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
    # the −15% hard stop must still fire instantly: −20% < 22% drop → pass through
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


def test_modest_rise_passes_through():
    # a +50% move is below max_rise (1.0 = +100%) → accepted immediately
    guard = {"X": {"last_good": 1.0, "pending": None}}
    assert eg.guarded_exit_price(guard, "X", 1.5) == 1.5
    assert guard["X"]["last_good"] == 1.5


def test_upward_spike_deferred_first_cycle():
    # >+100% in one cycle → suspect → act on last-good, stash pending (NOT the spike)
    guard = {"X": {"last_good": 1.0, "pending": None}}
    out = eg.guarded_exit_price(guard, "X", 3.0)   # +200%
    assert out == 1.0                              # NOT the spike price
    assert guard["X"]["pending"] == 3.0
    assert guard["X"]["last_good"] == 1.0


def test_recovery_clears_stale_pending():
    guard = {}
    eg.guarded_exit_price(guard, "X", 1.0)
    eg.guarded_exit_price(guard, "X", 0.10)        # suspect, pending set
    assert guard["X"]["pending"] == 0.10
    eg.guarded_exit_price(guard, "X", 0.95)        # recovery clears pending
    assert guard["X"]["pending"] is None
    assert guard["X"]["last_good"] == 0.95


def test_giga_2026_05_27_phantom_minus_32pct_deferred_then_discarded():
    # GIGA: real price ~$0.0037 flat (−3.5% h24, $1.8M liq), but one bad print read
    # $0.00249 (−32.7%) and fired the −15% stop across ~56 bots for ~$452 phantom.
    # The OLD 0.40 threshold let it through (−32% < −40%). With 0.22 it is deferred,
    # then the real price reverts next cycle → glitch discarded, no phantom stop.
    guard = {}
    eg.guarded_exit_price(guard, "GIGA", 0.003700)
    assert eg.guarded_exit_price(guard, "GIGA", 0.002490) == 0.003700  # deferred, NOT phantom
    assert guard["GIGA"]["pending"] == 0.002490
    assert eg.guarded_exit_price(guard, "GIGA", 0.003680) == 0.003680  # real price reverts → discard
    assert guard["GIGA"]["pending"] is None


def test_real_minus_32pct_dump_that_persists_still_fires():
    # The tighter threshold must NOT block a genuine fast dump: a real −32% move
    # that HOLDS confirms next cycle and the stop fires (one cycle late by design).
    guard = {}
    eg.guarded_exit_price(guard, "Y", 1.0)
    assert eg.guarded_exit_price(guard, "Y", 0.68) == 1.0    # −32% deferred one cycle
    assert eg.guarded_exit_price(guard, "Y", 0.66) == 0.66   # holds → confirmed → stop fires
    assert guard["Y"]["last_good"] == 0.66


# ── cross-source confirmation (confirm_fn) ──────────────────────────────────

def test_crosssource_disconfirms_glitch_acts_on_last_good_same_cycle():
    # GIGA: DS prints 0.00249 (−33%) but the independent source says ~0.00366
    # (healthy). Above the midpoint → glitch → ignore, act on last-good, no defer.
    guard = {"G": {"last_good": 0.0037, "pending": None}}
    out = eg.guarded_exit_price(guard, "G", 0.00249, confirm_fn=lambda: 0.00366)
    assert out == 0.0037                       # acted on last-good, NOT the glitch
    assert guard["G"]["pending"] is None       # resolved this cycle (no temporal defer)
    assert guard["G"]["last_good"] == 0.0037   # last_good NOT poisoned by the glitch


def test_crosssource_persistent_bad_source_stays_rejected():
    # The case temporal-only MISSES: the bad print persists 2+ cycles. Cross-source
    # keeps disconfirming → never fires a phantom stop, even cycle after cycle.
    guard = {"G": {"last_good": 0.0037, "pending": None}}
    for _ in range(3):
        out = eg.guarded_exit_price(guard, "G", 0.00249, confirm_fn=lambda: 0.00366)
        assert out == 0.0037                   # rejected every cycle
    assert guard["G"]["last_good"] == 0.0037


def test_crosssource_corroborates_real_move_acts_now():
    # Independent source also low (near the drop) → real move → act immediately,
    # no one-cycle latency.
    guard = {"R": {"last_good": 1.0, "pending": None}}
    out = eg.guarded_exit_price(guard, "R", 0.66, confirm_fn=lambda: 0.64)
    assert out == 0.66
    assert guard["R"]["last_good"] == 0.66


def test_crosssource_unavailable_falls_back_to_temporal():
    # confirm_fn returns None (fetch failed) → behave exactly like temporal guard.
    guard = {"X": {"last_good": 1.0, "pending": None}}
    assert eg.guarded_exit_price(guard, "X", 0.60, confirm_fn=lambda: None) == 1.0  # deferred
    assert guard["X"]["pending"] == 0.60


def test_crosssource_raises_falls_back_to_temporal():
    # confirm_fn raising must never propagate — fall back to temporal defer.
    def boom():
        raise RuntimeError("network down")
    guard = {"X": {"last_good": 1.0, "pending": None}}
    assert eg.guarded_exit_price(guard, "X", 0.60, confirm_fn=boom) == 1.0  # deferred, no raise
    assert guard["X"]["pending"] == 0.60


# ── upward-spike guard (2026-05-27 EURC phantom WIN) ────────────────────────

def test_eurc_2026_05_27_phantom_5316x_deferred_then_discarded():
    # EURC: a EUR stablecoin, real price ~$1.16 flat, but one print read $6199.37
    # (5,316x). The drop-only guard let it through → TP1+TP2 booked +$106,334 of
    # phantom profit. With the rise guard it is deferred, then the real price
    # reverts next cycle → glitch discarded, no phantom TP.
    guard = {}
    eg.guarded_exit_price(guard, "EURC", 1.1658)
    assert eg.guarded_exit_price(guard, "EURC", 6199.37) == 1.1658   # deferred, NOT the spike
    assert guard["EURC"]["pending"] == 6199.37
    assert eg.guarded_exit_price(guard, "EURC", 1.16) == 1.16        # real price reverts → discard
    assert guard["EURC"]["pending"] is None


def test_real_moon_that_persists_confirms_and_is_captured():
    # The rise guard must NOT block a genuine moon: a real +200% move that HOLDS
    # confirms next cycle and the TP can fire (one cycle late by design).
    guard = {}
    eg.guarded_exit_price(guard, "M", 1.0)
    assert eg.guarded_exit_price(guard, "M", 3.0) == 1.0    # +200% deferred one cycle
    assert eg.guarded_exit_price(guard, "M", 3.1) == 3.1    # holds → confirmed → TP captured
    assert guard["M"]["last_good"] == 3.1


def test_crosssource_disconfirms_upward_glitch_acts_on_last_good():
    # EURC: DS prints $6199 but the independent source still says ~$1.16 (healthy,
    # below the midpoint) → glitch → ignore, act on last-good, no phantom TP, no defer.
    guard = {"E": {"last_good": 1.1658, "pending": None}}
    out = eg.guarded_exit_price(guard, "E", 6199.37, confirm_fn=lambda: 1.16)
    assert out == 1.1658                       # acted on last-good, NOT the spike
    assert guard["E"]["pending"] is None       # resolved this cycle
    assert guard["E"]["last_good"] == 1.1658   # last_good NOT poisoned by the glitch


def test_crosssource_persistent_bad_high_source_stays_rejected():
    # Bad high print persists 2+ cycles — cross-source keeps disconfirming → never
    # books a phantom win, even cycle after cycle.
    guard = {"E": {"last_good": 1.1658, "pending": None}}
    for _ in range(3):
        out = eg.guarded_exit_price(guard, "E", 6199.37, confirm_fn=lambda: 1.16)
        assert out == 1.1658
    assert guard["E"]["last_good"] == 1.1658


def test_crosssource_corroborates_real_spike_acts_now():
    # Independent source also high (above midpoint) → real moon → act immediately.
    guard = {"R": {"last_good": 1.0, "pending": None}}
    out = eg.guarded_exit_price(guard, "R", 3.0, confirm_fn=lambda: 2.9)
    assert out == 3.0
    assert guard["R"]["last_good"] == 3.0


def test_drop_pending_then_opposite_spike_not_wrongly_confirmed():
    # A drop is deferred (pending below last_good); next cycle a glitch spike must
    # NOT be confirmed by the drop-direction pending — it re-defers instead.
    guard = {}
    eg.guarded_exit_price(guard, "X", 1.0)
    assert eg.guarded_exit_price(guard, "X", 0.20) == 1.0   # drop deferred, pending=0.20
    assert eg.guarded_exit_price(guard, "X", 5.0) == 1.0    # opposite-dir spike → re-defer, NOT confirm
    assert guard["X"]["pending"] == 5.0
    assert guard["X"]["last_good"] == 1.0
