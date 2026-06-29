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


# 7) FIX 5 — enforce is TWO-SIDED: with DEEP liquidity the liq-scaled slip is
#    BELOW the flat 1.5%, so the booked SELL price is HIGHER (better) than the old
#    flat-booked price (test 4 only covered thin -> worse).
def test_enforce_deep_liq_books_better_than_flat():
    off_booked = _flat_booked()
    booked, info = exit_slip_liq_book(
        "enforce", off_booked, MID, None, REASON, SIZE, 500_000.0,  # deep liq
        flat_slip_pct=FLAT, fee_usd=FEE)
    assert info["liq_available"] is True
    assert info["would_revert"] is False
    assert booked is not EXIT_HOLD
    assert info["liq_scaled_slip_pct"] < info["flat_slip_pct"]   # deep -> less slip
    assert booked == info["eff_exit_liqscaled"]
    assert booked > off_booked            # better (higher) sell fill booked
    assert info["exit_price_delta_pct"] > 0


# ---- call-site (DipScanner) harness: FIX 1 (partial-burn on HOLD) + FIX 3
#      (bounded retry / anti-strand) live on the PAPER sell path, not the pure
#      function, so they need the scanner skeleton (mirrors test_partial_burn). ----

import asyncio
from types import SimpleNamespace as NS

from feeds.dip_scanner import DipScanner


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _scanner_paper_exit(monkeypatch, exit_liq, bot_id="badday_flush",
                        sell_fraction=1.0, tp1_hit=False, tp2_hit=False):
    """A DipScanner skeleton wired so _execute_bot_sell takes the PAPER path
    (live_probe=False) and reaches the EXIT_SLIP_LIQ gate. ``exit_liq`` is the
    fresh exit-time liquidity the gate sees. close_position is spied via a counter.
    Returns (sc, pos, decision)."""
    # Default-off everywhere except the gate under test; PAPER_FIDELITY off so the
    # eff_exit is the plain flat sell_fill_price (no extra reprice block to mock).
    monkeypatch.setenv("PAPER_FIDELITY_MODE", "off")
    monkeypatch.setenv("EXIT_SLIP_LIQ_MODE", "enforce")

    sc = DipScanner.__new__(DipScanner)
    pos = NS(token="TOK", address="mintTOK", pair_address="pairTOK",
             entry_price=1.0, size_usd=20.0, entry_time=0.0,
             tp1_hit=tp1_hit, tp2_hit=tp2_hit,
             remaining_fraction=1.0, state_blob={})

    close_calls = []

    class _PM:
        def __init__(self):
            self.config = NS(bot_id=bot_id, live_probe=False)  # PAPER route

        def get_position(self, token):
            return pos

        def close_position(self, token, exit_price, exit_time, reason, sell_fraction):
            close_calls.append({"exit_price": exit_price, "sell_fraction": sell_fraction})
            # cost_usd<=0 -> _execute_bot_sell returns early (no capital plumbing).
            return NS(cost_usd=0.0, proceeds_usd=0.0)

    pm = _PM()
    sc.bot_position_managers = {bot_id: pm}
    sc.bot_capitals = {bot_id: NS(realize_sell=lambda cost_usd, proceeds_usd: None)}
    sc.trader = NS(private_key="")  # no key -> paper anyway
    sc._addr_by_token = {}

    # fresh exit liquidity the gate consumes
    sc._fresh_exit_liquidity = lambda addr: exit_liq

    async def _price(token, address="", pair_address=""):
        return 1.0
    sc._get_current_price_for = _price

    # capture shadow records in memory (real writer exercised in fail-open tests)
    sc._esl_recs = []
    sc._append_exit_slip_shadow = lambda rec: sc._esl_recs.append(rec)

    decision = NS(token="TOK", kind="HARD_STOP", reason="stop pnl=-12%",
                  sell_fraction=sell_fraction)
    sc._close_calls = close_calls
    return sc, pos, decision


# FIX 3 — after EXIT_SLIP_LIQ_MAX_HOLDS consecutive cap-exceed HOLDs the position
# BOOKS (close_position called) instead of holding forever; counter then resets.
def test_enforce_bounded_retry_books_after_max_holds(monkeypatch):
    monkeypatch.setenv("EXIT_SLIP_LIQ_MAX_HOLDS", "3")
    # exit_liq=1.0 -> modeled slip >> 6% cap -> would_revert every tick (HOLD).
    sc, pos, decision = _scanner_paper_exit(monkeypatch, exit_liq=1.0, sell_fraction=1.0)

    # ticks 1 and 2: HOLD (no booking), counter climbs
    for expected in (1, 2):
        _run(sc._execute_bot_sell("badday_flush", "TOK", decision, 1.0, 1000.0))
        assert sc._close_calls == []                       # held, not booked
        assert pos.state_blob.get("exit_slip_liq_holds") == expected

    # tick 3: counter hits MAX_HOLDS -> book-through (close_position called once)
    _run(sc._execute_bot_sell("badday_flush", "TOK", decision, 1.0, 1000.0))
    assert len(sc._close_calls) == 1                       # booked the flat exit
    assert pos.state_blob.get("exit_slip_liq_holds") == 0  # counter reset on book
    # the HOLD events were still structured-logged (FIX 2): 3 records, all would_revert
    assert len(sc._esl_recs) == 3
    assert all(r["would_revert"] is True for r in sc._esl_recs)


# FIX 1 — a HOLD on a PARTIAL (sell_fraction<1.0) must run the partial-burn
# handling (so the tp flag isn't silently burned); a FULL exit HOLD must not.
def test_enforce_partial_hold_calls_partial_burn(monkeypatch):
    monkeypatch.setenv("EXIT_SLIP_LIQ_MAX_HOLDS", "6")  # high -> first tick HOLDs
    sc, pos, decision = _scanner_paper_exit(
        monkeypatch, exit_liq=1.0, sell_fraction=0.75, tp1_hit=True)

    burn_calls = []
    sc._handle_partial_burn = (
        lambda bot_id, token, ed, position, current_price, now:
        burn_calls.append((bot_id, token, ed, position, current_price, now)))

    _run(sc._execute_bot_sell("badday_flush", "TOK", decision, 1.0, 1000.0))
    assert sc._close_calls == []          # HELD (non-fill)
    assert len(burn_calls) == 1           # partial-burn handling ran on the HOLD
    assert burn_calls[0][0] == "badday_flush"
    assert burn_calls[0][3] is pos        # called with the live-path signature


def test_enforce_full_hold_does_not_call_partial_burn(monkeypatch):
    monkeypatch.setenv("EXIT_SLIP_LIQ_MAX_HOLDS", "6")
    sc, pos, decision = _scanner_paper_exit(
        monkeypatch, exit_liq=1.0, sell_fraction=1.0)

    burn_calls = []
    sc._handle_partial_burn = (
        lambda *a, **k: burn_calls.append(a))

    _run(sc._execute_bot_sell("badday_flush", "TOK", decision, 1.0, 1000.0))
    assert sc._close_calls == []          # HELD
    assert burn_calls == []               # full exit -> no partial burn handling
