# -*- coding: utf-8 -*-
"""FAST PREFILTER (FIX 2 latency lever, 2026-06-17).

Production runs baseline_mode=true, which disables the cheap `continue` culls
in the dip-scan loop -> EVERY ~100-300 tokens reaches the heavy network fetch
(assemble_chart_data). The fast prefilter runs EVEN in baseline_mode using ONLY
the in-memory pair dict (no network) and culls tokens that CANNOT qualify for
ANY bot's entry. It must be strictly LOOSER than every downstream gate so it
never removes a buyable token.

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


def test_floor_well_below_lowest_lane():
    # default floor must be strictly below the lowest admission lane (badday
    # LIQ_MIN=$15k) so a badday-buyable token at $15k is NEVER culled.
    cull, _ = fast_prefilter_cull(15_000, 0, 0, 0, 1, 1)
    assert cull is False


# ---- (B) runaway-with-sellers -------------------------------------------

def test_runaway_sellers_culls():
    cull, reason = fast_prefilter_cull(50_000, 10, 10, 10, buys_m5=1, sells_m5=5)
    assert cull is True
    assert reason == "runaway_sellers"


def test_runaway_but_buyers_winning_not_culled():
    # running up but buyers still dominate = momentum-continuation candidate
    cull, _ = fast_prefilter_cull(50_000, 10, 10, 10, buys_m5=5, sells_m5=1)
    assert cull is False


def test_runaway_needs_all_three_timeframes():
    # only m5+h1 green, h6 flat -> not a confirmed runaway -> not culled
    cull, _ = fast_prefilter_cull(50_000, 10, 10, 0, buys_m5=1, sells_m5=5)
    assert cull is False


def test_dip_never_culled_by_runaway():
    # a deep dip with sellers is the EDGE, must never be culled
    cull, _ = fast_prefilter_cull(50_000, -20, -20, -20, buys_m5=1, sells_m5=5)
    assert cull is False


def test_no_m5_flow_not_runaway():
    # zero sells -> can't be "sellers winning" -> not culled even if green
    cull, _ = fast_prefilter_cull(50_000, 10, 10, 10, buys_m5=0, sells_m5=0)
    assert cull is False


def test_mild_green_not_culled():
    # +5% on all frames (below the +8% runaway bar) -> not culled
    cull, _ = fast_prefilter_cull(50_000, 5, 5, 5, buys_m5=1, sells_m5=5)
    assert cull is False


# ---- robustness ----------------------------------------------------------

def test_bad_inputs_fail_open():
    cull, _ = fast_prefilter_cull(50_000, "x", None, 10, "y", 5)
    assert cull is False
