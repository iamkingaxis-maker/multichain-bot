"""Tests for the EXIT_SLIP_LIQ liquidity-aware exit-slip / cap-revert gate.

The PAPER twin used to book every exit with a FLAT slip (measured_live_slip_pct,
default 1.5%) that is blind to liquidity, and never modeled that LIVE reverts a
sell when real price-impact exceeds the 6% sell cap. These tests pin the gate:
off = byte-identical, shadow = log-only, enforce = book the worse liq-scaled fill
and hold (non-fill) when the modeled slip >= the cap. Fail-open everywhere.
"""
import pytest

import core.paper_fidelity as pf
from core.paper_fidelity import (
    exit_slip_liq_eval,
    exit_slip_liq_book,
    liq_scaled_exit_slip_pct,
    paper_exit_decision,
    EXIT_HOLD,
)

MID = 0.10
SIZE = 20.0
FEE = 0.17
FLAT = 1.5            # measured_live_slip_pct default
REASON = "take_profit"  # non-stop -> no gap-through haircut, deterministic


def _flat_booked():
    """The exit price the OLD flat path books (what off must reproduce)."""
    return paper_exit_decision(MID, None, REASON, "enforce", SIZE,
                               slip_pct=FLAT, fee_usd=FEE)[0]


# 1) off -> byte-identical booked price, no info (=> no file written), for a
#    normal AND an illiquid case; the gate must short-circuit before any compute.
def test_off_is_byte_identical_normal_and_illiquid():
    current = _flat_booked()
    for liq in (50_000.0, 12.0, None):  # deep, thin, missing
        booked, info = exit_slip_liq_book(
            "off", current, MID, None, REASON, SIZE, liq,
            flat_slip_pct=FLAT, fee_usd=FEE)
        assert booked == current        # exact same booked exit price
        assert info is None             # no info -> caller writes nothing


# 2) shadow -> booked price UNCHANGED vs off, but info is returned with the
#    liq-scaled slip STRICTLY GREATER than the flat slip for a thin-liq input.
def test_shadow_books_old_but_liq_scaled_exceeds_flat_when_thin():
    current = _flat_booked()
    booked, info = exit_slip_liq_book(
        "shadow", current, MID, None, REASON, SIZE, 20.0,  # thin liq
        flat_slip_pct=FLAT, fee_usd=FEE)
    assert booked == current                       # P&L unchanged in shadow
    assert info is not None                         # a JSONL line would be logged
    assert info["liq_available"] is True
    assert info["liq_scaled_slip_pct"] > info["flat_slip_pct"]
    assert info["eff_exit_liqscaled"] < info["eff_exit_flat"]  # worse sell fill


# 3) shadow -> would_revert True when modeled liq-scaled slip >= 6% cap (very thin
#    liq + size); False for deep liquidity.
def test_would_revert_flag_thin_vs_deep():
    _, thin = exit_slip_liq_book("shadow", 0.0, MID, None, REASON, SIZE, 1.0,
                                 flat_slip_pct=FLAT, fee_usd=FEE)
    assert thin["would_revert"] is True
    assert thin["liq_scaled_slip_pct"] >= thin["sell_cap_pct"]

    _, deep = exit_slip_liq_book("shadow", 0.0, MID, None, REASON, SIZE, 500_000.0,
                                 flat_slip_pct=FLAT, fee_usd=FEE)
    assert deep["would_revert"] is False
    assert deep["liq_scaled_slip_pct"] < deep["sell_cap_pct"]


# 4) enforce (thin liq below cap) -> booked exit uses the worse liq-scaled slip,
#    so the booked SELL price is LOWER than the off-mode (flat) booked price.
def test_enforce_books_worse_liq_scaled_below_flat():
    off_booked = _flat_booked()
    booked, info = exit_slip_liq_book(
        "enforce", off_booked, MID, None, REASON, SIZE, 20.0,  # thin, below cap
        flat_slip_pct=FLAT, fee_usd=FEE)
    assert info["would_revert"] is False
    assert booked is not EXIT_HOLD
    assert booked == info["eff_exit_liqscaled"]
    assert booked < off_booked              # worse (lower) sell fill booked


# 5) enforce (modeled slip >= cap) -> exit is NOT booked this tick: the hold/retry
#    sentinel is returned (mirrors the live cap-revert non-fill).
def test_enforce_holds_when_slip_at_or_over_cap():
    booked, info = exit_slip_liq_book(
        "enforce", _flat_booked(), MID, None, REASON, SIZE, 1.0,  # very thin
        flat_slip_pct=FLAT, fee_usd=FEE)
    assert info["would_revert"] is True
    assert booked is EXIT_HOLD              # NON-FILL — position stays open


# 6a) fail-open: exit_liq=None -> behaves exactly like flat (no crash), booked
#     unchanged in every mode.
def test_fail_open_missing_liquidity_is_flat():
    current = _flat_booked()
    # liq_scaled helper returns None on missing/non-positive liq
    assert liq_scaled_exit_slip_pct(SIZE, None) is None
    assert liq_scaled_exit_slip_pct(SIZE, 0.0) is None

    for mode in ("shadow", "enforce"):
        booked, info = exit_slip_liq_book(
            mode, current, MID, None, REASON, SIZE, None,
            flat_slip_pct=FLAT, fee_usd=FEE)
        assert booked == current            # fail open to existing booking
        if info is not None:
            assert info["liq_available"] is False
            assert info["would_revert"] is False
            assert info["liq_scaled_slip_pct"] == info["flat_slip_pct"]
            assert info["eff_exit_liqscaled"] == info["eff_exit_flat"]


# 6b) fail-open: impact_pct_for_size raising -> flat fallback, no crash.
def test_fail_open_impact_raises(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("liquidity curve exploded")
    monkeypatch.setattr(pf, "impact_pct_for_size", _boom)

    current = _flat_booked()
    # eval must not raise and must degrade to flat
    info = exit_slip_liq_eval(MID, None, REASON, SIZE, 20.0,
                              flat_slip_pct=FLAT, fee_usd=FEE)
    assert info["liq_available"] is False
    assert info["would_revert"] is False
    assert info["eff_exit_liqscaled"] == info["eff_exit_flat"]

    booked, _ = exit_slip_liq_book(
        "enforce", current, MID, None, REASON, SIZE, 20.0,
        flat_slip_pct=FLAT, fee_usd=FEE)
    assert booked == current               # unchanged booking, no crash
