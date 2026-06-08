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


def test_cold_seed_with_entry_rejects_phantom_rise():
    # CDOF 2026-06-08: cold guard state (post-restart) + 62x phantom print + known
    # entry. The seed path must NOT blind-accept the phantom — it seeds last_good=entry
    # and validates against the OHLC high, rejecting it back to entry.
    guard = {}
    entry = 0.000163
    phantom = entry * 62          # 62x glitch
    out = eg.guarded_exit_price(
        guard, "CDOF", phantom,
        high_fn=lambda: entry * 1.2,   # real OHLC high ~1.2x entry
        ref_price=entry,
    )
    assert out == entry, f"phantom rise accepted on cold seed: {out}"
    assert guard["CDOF"]["last_decision"]["reason"] == "rise_rejected_above_high"


def test_cold_seed_with_entry_accepts_normal_first_print():
    # a normal first post-restart price (small move from entry) still passes through
    guard = {}
    out = eg.guarded_exit_price(guard, "Y", 1.05, high_fn=lambda: 1.5, ref_price=1.0)
    assert out == 1.05
    assert guard["Y"]["last_good"] == 1.05


def test_cold_seed_without_entry_ref_still_seeds():
    # no entry ref -> nothing to validate against -> seed-and-accept (unchanged)
    guard = {}
    assert eg.guarded_exit_price(guard, "Z", 999.0) == 999.0
    assert guard["Z"]["last_good"] == 999.0


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


def test_upward_spike_rejected_without_corroboration():
    # NEW POLICY 2026-06-01: a suspect rise with NO independent source is CAPPED at
    # last-good (not temporally deferred) — a phantom high must never book a fake win
    # on temporal-only confirmation. No pending is held for rises.
    guard = {"X": {"last_good": 1.0, "pending": None}}
    out = eg.guarded_exit_price(guard, "X", 3.0)   # +200%, no confirm_fn
    assert out == 1.0                              # capped at last-good, NOT the spike
    assert guard["X"]["last_good"] == 1.0          # not poisoned
    assert guard["X"]["pending"] is None           # rises never set a temporal pending


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
    assert eg.guarded_exit_price(guard, "EURC", 6199.37) == 1.1658   # capped, NOT the spike
    assert guard["EURC"]["pending"] is None                          # rises hold no pending
    assert eg.guarded_exit_price(guard, "EURC", 1.16) == 1.16        # real price reverts → normal
    assert guard["EURC"]["pending"] is None


def test_persistent_rise_without_crosssource_stays_capped():
    # NEW POLICY 2026-06-01: a rise is NEVER accepted on temporal-only. A sudden
    # +200% that persists WITHOUT independent corroboration stays CAPPED — the SPCX
    # phantom-prevention trade-off (a persistent bad source would otherwise be
    # temporally confirmed into a fake win).
    guard = {}
    eg.guarded_exit_price(guard, "M", 1.0)
    assert eg.guarded_exit_price(guard, "M", 3.0) == 1.0    # capped
    assert eg.guarded_exit_price(guard, "M", 3.1) == 1.0    # STILL capped (no temporal confirm)
    assert guard["M"]["last_good"] == 1.0


def test_real_moon_captured_via_crosssource():
    # A genuine moon IS still captured when the independent source corroborates.
    guard = {"M": {"last_good": 1.0, "pending": None}}
    out = eg.guarded_exit_price(guard, "M", 3.0, confirm_fn=lambda: 2.9)
    assert out == 3.0
    assert guard["M"]["last_good"] == 3.0


def test_gradual_climb_still_tps_normally():
    # A real gradual climb (each tick < max_rise) updates last_good every cycle, so
    # the position still TPs at the high even with no cross-source.
    guard = {}
    eg.guarded_exit_price(guard, "C", 1.0)
    assert eg.guarded_exit_price(guard, "C", 1.8) == 1.8   # +80% < +100% → accepted
    assert eg.guarded_exit_price(guard, "C", 3.2) == 3.2   # +78% from 1.8 → accepted
    assert guard["C"]["last_good"] == 3.2


def test_spcx_2026_06_01_persistent_rise_gt_down_no_phantom_win():
    # SPCX: real ~0.00092; a sticky bad print read 0.00384 (4.2x) for multiple cycles
    # while GeckoTerminal (confirm_fn) was 429'ing → returned None. Old code temporally
    # confirmed the persistent high → booked +$64 fake TP wins x3 premium bots. New
    # policy caps every cycle → no phantom TP.
    guard = {}
    eg.guarded_exit_price(guard, "SPCX", 0.00092)
    for _ in range(4):  # sticky glitch persists, GT down (confirm_fn None)
        assert eg.guarded_exit_price(guard, "SPCX", 0.00384, confirm_fn=lambda: None) == 0.00092
    assert guard["SPCX"]["last_good"] == 0.00092   # never books the 4.2x phantom


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


def test_drop_pending_then_opposite_spike_rejected_clears_pending():
    # A drop is deferred (pending below last_good); next cycle a glitch spike must
    # NOT be confirmed. Under the rise policy the spike is capped and pending cleared.
    guard = {}
    eg.guarded_exit_price(guard, "X", 1.0)
    assert eg.guarded_exit_price(guard, "X", 0.20) == 1.0   # drop deferred, pending=0.20
    assert eg.guarded_exit_price(guard, "X", 5.0) == 1.0    # spike → capped (rise policy)
    assert guard["X"]["pending"] is None                    # rise clears pending
    assert guard["X"]["last_good"] == 1.0


# ── PRIMARY rise check: real OHLC high (2026-06-01 BhTPX SPCX) ───────────────

def test_highfn_rejects_rise_above_real_high():
    # BhTPX SPCX: genuinely pumped (+1159%), real 24h high 0.00141, but a bad print
    # read 0.00384 (2.7x above the real high). high_fn says the token never traded
    # there → reject, act on last-good. No phantom TP even though confirm_fn is absent.
    guard = {"SPCX": {"last_good": 0.00092, "pending": None}}
    out = eg.guarded_exit_price(guard, "SPCX", 0.00384, high_fn=lambda: 0.00141)
    assert out == 0.00092                       # capped at last-good, NOT the glitch
    assert guard["SPCX"]["last_good"] == 0.00092
    assert guard["SPCX"]["pending"] is None


def test_highfn_accepts_rise_within_real_high():
    # A genuine spike that's within the token's real traded high → accept immediately.
    guard = {"X": {"last_good": 1.0, "pending": None}}
    out = eg.guarded_exit_price(guard, "X", 2.5, high_fn=lambda: 2.6)   # 2.5 <= 2.6*1.15
    assert out == 2.5
    assert guard["X"]["last_good"] == 2.5


def test_highfn_takes_precedence_over_crosssource_for_rise():
    # Even if a (glitching) second source would corroborate, the OHLC-high check
    # rejects a print above the real high first.
    guard = {"X": {"last_good": 1.0, "pending": None}}
    out = eg.guarded_exit_price(guard, "X", 5.0, high_fn=lambda: 2.0, confirm_fn=lambda: 5.0)
    assert out == 1.0   # high_fn rejects (5.0 > 2.0*1.15) before confirm_fn is consulted


def test_highfn_unavailable_falls_back_to_crosssource():
    # high_fn None → use confirm_fn. Corroborated → accept.
    guard = {"X": {"last_good": 1.0, "pending": None}}
    out = eg.guarded_exit_price(guard, "X", 3.0, high_fn=lambda: None, confirm_fn=lambda: 2.9)
    assert out == 3.0


def test_highfn_unavailable_and_no_crosssource_rejects_rise():
    # Both unavailable → rise rejected (never temporal-only).
    guard = {"X": {"last_good": 1.0, "pending": None}}
    out = eg.guarded_exit_price(guard, "X", 3.0, high_fn=lambda: None)
    assert out == 1.0
    assert guard["X"]["pending"] is None


def test_highfn_does_not_affect_drops():
    # high_fn is rise-only; a suspect drop still uses cross-source/temporal.
    guard = {"X": {"last_good": 1.0, "pending": None}}
    # drop with confirm_fn disconfirming → reject (act on last-good), high_fn ignored
    out = eg.guarded_exit_price(guard, "X", 0.5, high_fn=lambda: 0.0001, confirm_fn=lambda: 0.98)
    assert out == 1.0


# ── PRIMARY drop check: real OHLC low (symmetric, 2026-06-01 E6ifp2 SPCX) ─────

def test_lowfn_rejects_stop_below_real_low():
    # E6ifp2 SPCX: real recent low 0.00313, but a glitch tick filled the stop at
    # 0.0008 (4x below). low_fn says the token never traded there → reject, no
    # phantom stop, even with no confirm_fn.
    guard = {"SPCX": {"last_good": 0.00417, "pending": None}}
    out = eg.guarded_exit_price(guard, "SPCX", 0.0008, low_fn=lambda: 0.00313)
    assert out == 0.00417                       # acted on last-good, NOT the glitch
    assert guard["SPCX"]["last_good"] == 0.00417
    assert guard["SPCX"]["pending"] is None


def test_lowfn_accepts_stop_within_real_low():
    # A genuine fast dump whose price is within the token's real recent low → fire.
    guard = {"X": {"last_good": 1.0, "pending": None}}
    out = eg.guarded_exit_price(guard, "X", 0.5, low_fn=lambda: 0.48)   # 0.5 >= 0.48*0.85
    assert out == 0.5
    assert guard["X"]["last_good"] == 0.5


def test_lowfn_unavailable_falls_back_to_temporal_drop():
    # low_fn None → drop uses the existing temporal path (real rug still fires).
    guard = {"X": {"last_good": 1.0, "pending": None}}
    assert eg.guarded_exit_price(guard, "X", 0.5, low_fn=lambda: None) == 1.0   # deferred
    assert guard["X"]["pending"] == 0.5
    assert eg.guarded_exit_price(guard, "X", 0.49, low_fn=lambda: None) == 0.49  # holds → fires


def test_lowfn_does_not_affect_rises():
    # low_fn is drop-only; a suspect rise still uses high_fn/confirm.
    guard = {"X": {"last_good": 1.0, "pending": None}}
    out = eg.guarded_exit_price(guard, "X", 3.0, low_fn=lambda: 0.0001, high_fn=lambda: 5.0)
    assert out == 3.0   # within real high (5.0) → accepted; low_fn ignored for rise


# ── ABSOLUTE-move guard via ref_price (2026-06-02 overnight phantoms) ─────────
# The single-cycle suspect_rise/drop triggers (>+100% / >-22% tick-to-tick) miss a
# GRADUAL multi-cycle climb to a glitch (each step under the threshold) and a
# below-real-low drop confirmed only temporally. Passing ref_price=entry enables an
# ABSOLUTE-from-entry trigger that consults the OHLC bound regardless of per-cycle
# delta, gated to NEW extremes (high-/low-water marks) so egress stays bounded.

def test_refprice_gradual_climb_above_real_high_rejected():
    # SPCX 2026-06-02: entry 0.00076; a bad feed climbed GRADUALLY (each tick <+100%):
    # 0.00076 → 0.0015 → 0.0029 → 0.0039 (4.7x). No single-cycle suspect_rise ever
    # fired, so the OHLC-high check never ran and TP booked +374%. With ref_price the
    # absolute trigger validates each NEW high against the real OHLC high (0.001415).
    guard = {}
    entry = 0.00076
    hi = lambda: 0.001415
    eg.guarded_exit_price(guard, "SPCX", entry, ref_price=entry, high_fn=hi)
    # 0.0015 (~2x entry): within high*1.15=0.001627 → accepted, becomes the new high.
    assert eg.guarded_exit_price(guard, "SPCX", 0.0015, ref_price=entry, high_fn=hi) == 0.0015
    # 0.0029: +93% from 0.0015 (NOT single-cycle suspect) but a new high above
    # high*1.15 → absolute trigger rejects (pre-fix this was accepted → poison).
    assert eg.guarded_exit_price(guard, "SPCX", 0.0029, ref_price=entry, high_fn=hi) == 0.0015
    # 0.0039: still above the real high → rejected.
    assert eg.guarded_exit_price(guard, "SPCX", 0.0039, ref_price=entry, high_fn=hi) == 0.0015
    assert guard["SPCX"]["last_good"] == 0.0015   # last_good never poisoned to the glitch


def test_refprice_plateau_winner_not_rechecked_bounds_egress():
    # A real winner sitting above +50% must NOT re-call high_fn every cycle — the
    # absolute trigger fires only on a NEW high-water mark, so a plateau is free.
    calls = []
    def hi():
        calls.append(1); return 2.0
    guard = {}
    eg.guarded_exit_price(guard, "W", 1.0, ref_price=1.0, high_fn=hi)
    assert eg.guarded_exit_price(guard, "W", 1.8, ref_price=1.0, high_fn=hi) == 1.8  # new high → 1 call
    assert eg.guarded_exit_price(guard, "W", 1.8, ref_price=1.0, high_fn=hi) == 1.8  # plateau → no call
    assert eg.guarded_exit_price(guard, "W", 1.7, ref_price=1.0, high_fn=hi) == 1.7  # lower → no call
    assert calls == [1]


def test_refprice_disabled_when_no_ref_preserves_gradual_climb():
    # Backward-compat: with ref_price absent the absolute trigger is OFF and a gradual
    # climb still TPs exactly as before (mirrors test_gradual_climb_still_tps_normally).
    guard = {}
    eg.guarded_exit_price(guard, "C", 1.0, high_fn=lambda: 1.5)
    assert eg.guarded_exit_price(guard, "C", 1.8, high_fn=lambda: 1.5) == 1.8   # accepted, no abs check
    assert eg.guarded_exit_price(guard, "C", 3.2, high_fn=lambda: 1.5) == 3.2
    assert guard["C"]["last_good"] == 3.2


def test_refprice_catastrophic_drop_no_ohlc_never_temporal_only():
    # Buttcoin 2026-06-02: entry 0.0148; a near-zero print (2.35e-6) with GeckoTerminal
    # down (low_fn → None) and no cross-source got TEMPORALLY confirmed over 2 cycles and
    # booked -100%. A below-(-50%)-from-entry drop is catastrophic → like a rise it must
    # NEVER be accepted on temporal-only; require OHLC or cross-source corroboration.
    guard = {}
    entry = 0.0148
    eg.guarded_exit_price(guard, "BUTT", entry, ref_price=entry)
    for _ in range(3):   # sticky near-zero, GT down
        assert eg.guarded_exit_price(guard, "BUTT", 2.35e-6, ref_price=entry,
                                     low_fn=lambda: None) == entry
    assert guard["BUTT"]["last_good"] == entry   # never books the -100% phantom


def test_refprice_catastrophic_drop_with_real_low_still_fires():
    # A GENUINE rug below -50% still fires the moment the OHLC low corroborates (the
    # real low IS near zero), so catastrophic-drop hardening never traps a real loss.
    guard = {}
    entry = 0.0148
    eg.guarded_exit_price(guard, "RUG", entry, ref_price=entry)
    out = eg.guarded_exit_price(guard, "RUG", 1e-6, ref_price=entry, low_fn=lambda: 1e-6)
    assert out == 1e-6
    assert guard["RUG"]["last_good"] == 1e-6


def test_refprice_catastrophic_drop_crosssource_corroborates_fires():
    # Catastrophic drop with no OHLC but an independent source that AGREES (also near
    # zero) → corroborated → fires immediately (cross-source counts, only temporal-only
    # is barred).
    guard = {}
    entry = 1.0
    eg.guarded_exit_price(guard, "Z", entry, ref_price=entry)
    out = eg.guarded_exit_price(guard, "Z", 0.02, ref_price=entry,
                                low_fn=lambda: None, confirm_fn=lambda: 0.018)
    assert out == 0.02


def test_refprice_modest_drop_below_50pct_threshold_unaffected():
    # A -30% drop (above the -50% catastrophic line) with ref_price still uses the
    # normal temporal/low path — the catastrophic rule only governs deep drops.
    guard = {}
    entry = 1.0
    eg.guarded_exit_price(guard, "D", entry, ref_price=entry)
    assert eg.guarded_exit_price(guard, "D", 0.70, ref_price=entry, low_fn=lambda: None) == 1.0  # deferred
    assert eg.guarded_exit_price(guard, "D", 0.69, ref_price=entry, low_fn=lambda: None) == 0.69  # holds → fires


# ── decision instrumentation (2026-06-02): guard[token]["last_decision"] ──────
# Every call records WHY it returned what it did, so dip_scanner can stamp it onto
# the sell record and a phantom that ever slips is diagnosable from data.

def test_decision_recorded_on_seed():
    guard = {}
    eg.guarded_exit_price(guard, "X", 1.0)
    d = guard["X"]["last_decision"]
    assert d["reason"] == "seed" and d["raw"] == 1.0 and d["ret"] == 1.0


def test_decision_recorded_on_normal_move():
    guard = {"X": {"last_good": 1.0, "pending": None}}
    eg.guarded_exit_price(guard, "X", 0.95)
    d = guard["X"]["last_decision"]
    assert d["reason"] == "normal" and d["ret"] == 0.95 and d["suspect_rise"] is False


def test_decision_recorded_rise_rejected_with_high_val():
    guard = {"SPCX": {"last_good": 0.00092, "pending": None}}
    eg.guarded_exit_price(guard, "SPCX", 0.00384, high_fn=lambda: 0.00141)
    d = guard["SPCX"]["last_decision"]
    assert d["reason"] == "rise_rejected_above_high"
    assert d["high_val"] == 0.00141 and d["ret"] == 0.00092 and d["suspect_rise"] is True


def test_decision_recorded_drop_rejected_with_low_val():
    guard = {"SPCX": {"last_good": 0.00417, "pending": None}}
    eg.guarded_exit_price(guard, "SPCX", 0.0008, low_fn=lambda: 0.00313)
    d = guard["SPCX"]["last_decision"]
    assert d["reason"] == "drop_rejected_below_low"
    assert d["low_val"] == 0.00313 and d["ret"] == 0.00417


def test_decision_recorded_catastrophic_drop():
    guard = {}
    entry = 0.0148
    eg.guarded_exit_price(guard, "BUTT", entry, ref_price=entry)
    eg.guarded_exit_price(guard, "BUTT", 2.35e-6, ref_price=entry, low_fn=lambda: None)
    d = guard["BUTT"]["last_decision"]
    assert d["reason"] == "catastrophic_drop_no_corroboration"
    assert d["catastrophic_drop"] is True and d["ret"] == entry


def test_decision_records_gradual_climb_abs_rise_flag():
    # the SPCX overnight gap: abs_rise_hit set even though no single-cycle suspect.
    guard = {}
    entry = 0.00076
    hi = lambda: 0.001415
    eg.guarded_exit_price(guard, "SPCX", entry, ref_price=entry, high_fn=hi)
    eg.guarded_exit_price(guard, "SPCX", 0.0015, ref_price=entry, high_fn=hi)   # accepted
    eg.guarded_exit_price(guard, "SPCX", 0.0029, ref_price=entry, high_fn=hi)   # rejected
    d = guard["SPCX"]["last_decision"]
    assert d["abs_rise_hit"] is True and d["reason"] == "rise_rejected_above_high"
    assert d["high_val"] == 0.001415
