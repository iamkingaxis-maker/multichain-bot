# -*- coding: utf-8 -*-
"""FAST PREFILTER (FIX 2 latency lever, 2026-06-17; Part B signal-neutral recal).

Production runs baseline_mode=true, which disables the cheap `continue` culls
in the dip-scan loop -> EVERY ~100-300 tokens reaches the heavy network fetch
(assemble_chart_data). The fast prefilter runs EVEN in baseline_mode using ONLY
the in-memory pair dict (no network) and culls tokens that CANNOT qualify for
ANY enabled bot's entry. It must be a STRICT SUBSET of the gates every enabled
bot already enforces so it never removes a buyable token.

Part B recalibration: the cull is now SIGNAL-NEUTRAL. It mirrors ONLY the one
hard, non-directional gate every enabled bot shares — a minimum-liquidity floor
set strictly BELOW the lowest enabled-bot liq floor (fleet anti-rug $25k; lowest
antirug-EXEMPT lane rugpocket_scalper $12k entry_gate / $9k real habitat; default
prefilter floor $8k). The prior `runaway_sellers` momentum rule was REMOVED — it
culled real winners (BRIM h1 +59%, Monkey h1 +90%, both fired buys).

Behind FAST_PREFILTER_MODE=off|shadow|enforce (default shadow). These tests
drive the pure predicate `fast_prefilter_cull`."""
from feeds.dip_scanner import fast_prefilter_cull


# ---- (A) liquidity floor -------------------------------------------------

def test_low_liq_culls():
    cull, reason = fast_prefilter_cull(5_000, 0, 0, 0, 1, 1)
    assert cull is True
    assert reason == "low_liq"


def test_liq_at_floor_not_culled():
    # exactly at the floor is NOT below it -> not culled
    cull, _ = fast_prefilter_cull(8_000, 0, 0, 0, 1, 1, min_liq_usd=8_000)
    assert cull is False


def test_liq_above_floor_not_culled():
    cull, _ = fast_prefilter_cull(50_000, 0, 0, 0, 1, 1)
    assert cull is False


def test_zero_liq_fails_open():
    # liq<=0 is UNKNOWN, not "below floor" -> never cull on liquidity
    cull, _ = fast_prefilter_cull(0, 0, 0, 0, 1, 1)
    assert cull is False
    cull, _ = fast_prefilter_cull(None, 0, 0, 0, 1, 1)
    assert cull is False


def test_floor_strictly_below_lowest_enabled_lane():
    # The most-permissive enabled bot (rugpocket_scalper, antirug_floor_exempt)
    # buys at liq>=$12k entry_gate and its real habitat dips to ~$9k. The
    # default prefilter floor ($8k) must sit strictly below that, so a token
    # ANY enabled bot could buy is never culled.
    for liq in (9_000, 12_000, 15_000):  # rugpocket habitat / entry_gate / badday floor
        cull, _ = fast_prefilter_cull(liq, 0, 0, 0, 1, 1)
        assert cull is False, f"liq=${liq} is buyable by an enabled bot, must NOT cull"


# ---- (B) signal-neutrality: momentum is NEVER a disqualifier -------------

def test_runaway_momentum_no_longer_culled():
    # The OLD runaway_sellers rule culled this (m5/h1/h6 all >+8% + sellers
    # winning). It dropped real winners -> REMOVED. Must NOT cull.
    cull, reason = fast_prefilter_cull(50_000, 10, 10, 10, buys_m5=1, sells_m5=5)
    assert cull is False
    assert reason is None


def test_brim_case_not_culled():
    # BRIM fired a buy at h1 +59% — a runner, not unbuyable. Never cull.
    cull, _ = fast_prefilter_cull(50_000, pc_m5=5, pc_h1=59, pc_h6=40,
                                  buys_m5=2, sells_m5=8)
    assert cull is False


def test_monkey_case_not_culled():
    # Monkey fired a buy at h1 +90%. Never cull.
    cull, _ = fast_prefilter_cull(80_000, pc_m5=8, pc_h1=90, pc_h6=60,
                                  buys_m5=1, sells_m5=9)
    assert cull is False


def test_dip_never_culled():
    # a deep dip with sellers is the EDGE, must never be culled
    cull, _ = fast_prefilter_cull(50_000, -20, -20, -20, buys_m5=1, sells_m5=5)
    assert cull is False


def test_momentum_above_floor_never_culled():
    # any liq-passing token, regardless of price action / flow, is kept
    for m5, h1, h6, b, s in [
        (10, 10, 10, 0, 0),
        (5, 5, 5, 1, 5),
        (100, 100, 100, 0, 50),
        (-50, 30, -10, 3, 3),
    ]:
        cull, _ = fast_prefilter_cull(50_000, m5, h1, h6, buys_m5=b, sells_m5=s)
        assert cull is False, f"momentum/flow must never cull: {(m5,h1,h6,b,s)}"


# ---- robustness ----------------------------------------------------------

def test_bad_inputs_fail_open():
    cull, _ = fast_prefilter_cull(50_000, "x", None, 10, "y", 5)
    assert cull is False
    # bad liq value -> treated as 0 -> UNKNOWN -> fail-open
    cull, _ = fast_prefilter_cull("bad", 0, 0, 0, 1, 1)
    assert cull is False
