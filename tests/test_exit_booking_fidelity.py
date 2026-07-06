# -*- coding: utf-8 -*-
"""EXIT-BOOKING FIDELITY regression tests (2026-07-06, 公牛 twin).

Tonight's first paired live/paper round trip (token 公牛, badday_young_absorb,
02:49-02:51Z) exposed a two-price split on the paper twin's velocity-bail:
the decision fired at fresh pnl -9.34% but the BOOKED pnl was -2.70% — the
paper-fidelity CLAMP-TO-LOW raised the booking to the STALE slow-tick MAE
(-2.7037%, stamped 58s before the flush). Two mechanisms, both pinned here:

  1. STALE-MAE CLAMP: mae_pct is stamped only by the slow sweep, so on a
     fast-path ([reprice]) bail it can sit ABOVE the fired decision price and
     the clamp erased the flush entirely (公牛: -9.34 -> booked -2.70).
  2. FRICTION ERASURE: on slow-path bails the MAE is stamped AT the decision
     tick == the decision price, so the old post-friction clamp raised the
     booking back to EXACTLY the decision price — zero slip/fee ever paid
     (HANDSEM/ACM/trumplet ledger records: booked pnl == decision pnl to 4dp).
  3. REFETCH OVERRIDE: _execute_bot_sell re-fetched a "fresh" price and booked
     that instead of the decision price the firing rule evaluated (ALYCIACOW:
     decision -9.69 -> booked -3.47 off a post-decision bounce/lagging source).

The fix: booking basis = the DECISION price (fast paths pass fresh in; the
slow sweep passes its decision price), the clamp bounds only the gap-through
component (never above the decision print), and slip+fee friction is applied
BELOW the clamp — calibrated from the real live SELL legs (live_swaps.jsonl),
fail-open to a conservative 1.0%/leg when the live sample is thin.

Harness mirrors tests/test_paper_fidelity_wire_integration.py (drives the REAL
DipScanner._execute_bot_sell — no re-implementation).
"""
import asyncio
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace as NS
from collections import OrderedDict

from feeds.dip_scanner import DipScanner
from core.bot_config import BotConfig
from core.per_bot_capital import PerBotCapital
from core.per_bot_position_manager import PerBotPositionManager, ExitDecision
from core.slippage_model import sell_fill_price
from core.paper_fidelity import (
    effective_fill,
    paper_exit_decision,
    gap_through_extra_pct,
)
import core.fill_calibration as fc

BOT_ID = "exitfid_test_bot"
ADDR = "MintAaaBbbCccDddEeeFffGggHhhIiiJjjKkkLpump"
ENTRY = 1.00
SIZE = 30.0
FEE = 0.17
SLIP_PLACEHOLDER = 1.5   # PAPER_LIVE_SLIP_PCT pin
BAIL_REASON = "in-flight velocity-bail pnl=-9.34% vel=0.0849pp/s (floor -7)"
BAIL_REASON_FAST = BAIL_REASON + " [reprice]"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_scanner(fresh_price):
    """DipScanner shell with real per-bot collaborators; price seams stubbed.
    ``fresh_price`` is what a REFETCH would return — the fix must IGNORE it."""
    sc = DipScanner.__new__(DipScanner)
    cfg = BotConfig(bot_id=BOT_ID, display_name="exit fidelity test")
    pm = PerBotPositionManager(cfg)
    cap = PerBotCapital(bot_id=BOT_ID, starting_balance_usd=2000.0)
    sc.bot_capitals = {BOT_ID: cap}
    sc.bot_position_managers = {BOT_ID: pm}
    sc.trader = NS(private_key="")          # paper route
    sc._addr_by_token = OrderedDict()
    sc._fast_armed = {}
    sc._buy_gate = None
    sc._token_registry = None
    sc._exit_price_guard = {}
    sc._exit_price_guard_ts = {}
    sc._cycle_sol_features = {}
    sc.min_mcap = 1_000_000
    sc._user_watchlist_addrs = set()
    sc.trade_store = None
    sc.pool_price_feed = None
    sc.open_positions_ref = {}

    async def _fake_fresh(token, address="", pair_address=""):
        return fresh_price

    sc._get_current_price_for = _fake_fresh
    sc._fast_price_for = lambda addr, jup: (jup, "jupiter")
    sc._log_fill_speed_record = lambda *a, **k: None
    sc._sol_flk_1h = lambda now=None: 0
    return sc, pm, cap


def _open_position(pm, mae_pct=None):
    pos = pm.open_position(
        token="TOK", entry_price=ENTRY, size_usd=SIZE,
        entry_time=time.time(), address=ADDR, pair_address="pairTOK",
    )
    pos.state_blob["slip_pct"] = 1.0
    if mae_pct is not None:
        pos.state_blob["mae_pct"] = mae_pct
        pos.state_blob["mae_at_secs"] = 1
    return pos


def _set_env(monkeypatch, calibration="off"):
    monkeypatch.setenv("PAPER_FIDELITY_MODE", "enforce")
    monkeypatch.setenv("PAPER_LIVE_SLIP_PCT", str(SLIP_PLACEHOLDER))
    monkeypatch.setenv("PAPER_FEE_USD_PER_TX", str(FEE))
    monkeypatch.setenv("GAP_THROUGH_HAIRCUT_PCT", "5.0")
    monkeypatch.setenv("FILL_CALIBRATION_ENABLED", calibration)
    monkeypatch.setenv("ULTRA_FEE_MODEL", "off")
    monkeypatch.setenv("EXIT_SLIP_LIQ_MODE", "off")
    monkeypatch.setenv("EXIT_ROUTE_DEAD_MODE", "off")


def _booked_exit(cap):
    """Recover the booked exit price from the realized capital delta (position
    seated without reservation -> proceeds = size * exit/entry)."""
    proceeds = cap.balance_usd - 2000.0
    return (proceeds / SIZE) * ENTRY


def _expected_booking(decision_price, slip_pct, reason):
    basis = decision_price * (1.0 - gap_through_extra_pct(reason) / 100.0)
    return effective_fill(basis, "sell", slip_pct, FEE, SIZE)


# ── (a) SLOW PATH regression: booked pnl == decision pnl minus modeled friction ──

def test_slow_path_bail_books_decision_minus_friction(monkeypatch):
    """Slow-path velocity-bail: MAE is stamped AT the decision tick == the
    decision price. Pre-fix, the post-friction clamp raised the booking to
    EXACTLY the decision price (zero slip/fee). Now: booked exit pnl ==
    decision pnl minus modeled exit friction."""
    _set_env(monkeypatch)
    decision_price = 0.91                      # -9.00% decision pnl
    sc, pm, cap = _make_scanner(fresh_price=decision_price)
    _open_position(pm, mae_pct=-9.0)           # slow tick stamped mae == decision
    ed = ExitDecision(token="TOK", kind="IN_FLIGHT_FLOOR",
                      reason=BAIL_REASON, sell_fraction=1.0)
    _run(sc._execute_bot_sell(BOT_ID, "TOK", ed, decision_price, time.time()))

    booked = _booked_exit(cap)
    expected = _expected_booking(decision_price, SLIP_PLACEHOLDER, BAIL_REASON)
    assert booked == pytest.approx(expected, rel=1e-9)
    # friction is PAID: strictly below the decision print (the pre-fix bug
    # booked decision_price exactly).
    assert booked < decision_price
    # pnl-space pin: booked pnl = decision pnl - drag_pct * (price ratio)
    decision_pnl = (decision_price / ENTRY - 1.0) * 100.0
    booked_pnl = (booked / ENTRY - 1.0) * 100.0
    drag_pct = SLIP_PLACEHOLDER + FEE / SIZE * 100.0
    assert booked_pnl == pytest.approx(
        decision_pnl - drag_pct * (decision_price / ENTRY), rel=1e-9)


# ── (a') FAST PATH / 公牛 regression: stale MAE cannot erase the flush ──────────

def test_stale_mae_cannot_raise_booking_above_decision(monkeypatch):
    """公牛 shape: fast-path bail fired at -9.34% while the slow-tick MAE was a
    stale -2.70%. Pre-fix the clamp booked entry*(1-0.027) (-2.70%); the fix
    books the decision price minus friction."""
    _set_env(monkeypatch)
    decision_price = 0.9066                    # -9.34% decision pnl
    sc, pm, cap = _make_scanner(fresh_price=decision_price)
    _open_position(pm, mae_pct=-2.7037)        # STALE slow-tick MAE
    ed = ExitDecision(token="TOK", kind="IN_FLIGHT_FLOOR",
                      reason=BAIL_REASON_FAST, sell_fraction=1.0)
    _run(sc._execute_bot_sell(BOT_ID, "TOK", ed, decision_price, time.time()))

    booked = _booked_exit(cap)
    expected = _expected_booking(decision_price, SLIP_PLACEHOLDER, BAIL_REASON_FAST)
    assert booked == pytest.approx(expected, rel=1e-9)
    old_bug_value = ENTRY * (1.0 - 2.7037 / 100.0)   # what pre-fix booked
    assert booked < decision_price < old_bug_value
    assert abs(booked - old_bug_value) / old_bug_value > 0.05  # ~6.6pp apart


def test_fast_path_books_fired_fresh_price_not_refetch(monkeypatch):
    """Fast paths pass the fresh price the rule evaluated as current_price.
    Booking must base on EXACTLY that — a wildly different refetchable price
    (the stubbed _get_current_price_for returns 9.99) must be IGNORED."""
    _set_env(monkeypatch)
    decision_price = 0.9066
    sc, pm, cap = _make_scanner(fresh_price=9.99)   # lagging/bounced refetch
    _open_position(pm, mae_pct=None)
    ed = ExitDecision(token="TOK", kind="IN_FLIGHT_FLOOR",
                      reason=BAIL_REASON_FAST, sell_fraction=1.0)
    _run(sc._execute_bot_sell(BOT_ID, "TOK", ed, decision_price, time.time()))

    booked = _booked_exit(cap)
    expected = _expected_booking(decision_price, SLIP_PLACEHOLDER, BAIL_REASON_FAST)
    assert booked == pytest.approx(expected, rel=1e-9)
    assert booked < 1.0   # nowhere near the 9.99 refetch


# ── (b) NO DOUBLE-HAIRCUT ───────────────────────────────────────────────────────

def test_no_double_haircut(monkeypatch):
    """Enforce books the fidelity value DIRECTLY: exactly one application of
    slip+fee on the decision basis — never sell_fill_price THEN fidelity."""
    _set_env(monkeypatch)
    decision_price = 0.91
    sc, pm, cap = _make_scanner(fresh_price=decision_price)
    _open_position(pm)
    ed = ExitDecision(token="TOK", kind="IN_FLIGHT_FLOOR",
                      reason=BAIL_REASON, sell_fraction=1.0)
    _run(sc._execute_bot_sell(BOT_ID, "TOK", ed, decision_price, time.time()))

    booked = _booked_exit(cap)
    single = _expected_booking(decision_price, SLIP_PLACEHOLDER, BAIL_REASON)
    # double-charged candidate: slippage_model haircut THEN the fidelity fill
    double = effective_fill(sell_fill_price(decision_price, SIZE, 1.0),
                            "sell", SLIP_PLACEHOLDER, FEE, SIZE)
    assert booked == pytest.approx(single, rel=1e-9)
    assert abs(booked - double) > 1e-6, "booked matches the double-haircut value"


# ── exit-side FILL CALIBRATION (sell legs of live_swaps.jsonl) ──────────────────

def test_exit_calibration_slip_used_when_available(monkeypatch):
    """FILL_CALIBRATION_ENABLED=on + a fat sell-leg sample => the booked exit
    pays the CALIBRATED sell slip, not the 1.5% placeholder."""
    _set_env(monkeypatch, calibration="on")
    monkeypatch.setattr(
        fc, "load_exit_calibration",
        lambda: {"overall": {"slip_p50": 2.5, "slip_p90": 4.0,
                             "runup_p50": None, "n": 10}})
    decision_price = 0.91
    sc, pm, cap = _make_scanner(fresh_price=decision_price)
    _open_position(pm)
    ed = ExitDecision(token="TOK", kind="IN_FLIGHT_FLOOR",
                      reason=BAIL_REASON, sell_fraction=1.0)
    _run(sc._execute_bot_sell(BOT_ID, "TOK", ed, decision_price, time.time()))

    booked = _booked_exit(cap)
    expected = _expected_booking(decision_price, 2.5, BAIL_REASON)
    placeholder = _expected_booking(decision_price, SLIP_PLACEHOLDER, BAIL_REASON)
    assert booked == pytest.approx(expected, rel=1e-9)
    assert booked < placeholder   # 2.5% calibrated > 1.5% placeholder drag


def test_exit_calibration_thin_falls_open_to_conservative_default(monkeypatch):
    """Thin/absent sell-leg calibration => conservative 1.0%/leg default
    (PAPER_EXIT_SLIP_DEFAULT_PCT), NOT the buy-side placeholder."""
    _set_env(monkeypatch, calibration="on")
    monkeypatch.setattr(fc, "load_exit_calibration", lambda: {})
    decision_price = 0.91
    sc, pm, cap = _make_scanner(fresh_price=decision_price)
    _open_position(pm)
    ed = ExitDecision(token="TOK", kind="IN_FLIGHT_FLOOR",
                      reason=BAIL_REASON, sell_fraction=1.0)
    _run(sc._execute_bot_sell(BOT_ID, "TOK", ed, decision_price, time.time()))

    booked = _booked_exit(cap)
    expected = _expected_booking(decision_price, 1.0, BAIL_REASON)
    assert booked == pytest.approx(expected, rel=1e-9)


def test_load_exit_calibration_reads_sell_legs_only(tmp_path, monkeypatch):
    """load_exit_calibration aggregates ONLY successful sell records; the
    buy-side load_calibration cache is untouched."""
    monkeypatch.setattr(fc, "_EXIT_CACHE", {})
    monkeypatch.setattr(fc, "_CACHE", {})
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from core.live_swap_log import LOG_BASENAME
    recs = (
        [{"side": "sell", "success": True, "fill_vs_mid_slippage_pct": 1.11,
          "liquidity_usd": 40000.0}] * 6
        + [{"side": "buy", "success": True, "fill_vs_mid_slippage_pct": 0.4,
            "liquidity_usd": 40000.0}] * 6
        + [{"side": "sell", "success": False, "fill_vs_mid_slippage_pct": 9.0,
            "liquidity_usd": 40000.0}]
    )
    with open(os.path.join(str(tmp_path), LOG_BASENAME), "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")

    xcal = fc.load_exit_calibration()
    assert xcal["overall"]["n"] == 6
    assert xcal["overall"]["slip_p50"] == pytest.approx(1.11)
    bcal = fc.load_calibration()
    assert bcal["overall"]["n"] == 6
    assert bcal["overall"]["slip_p50"] == pytest.approx(0.4)


# ── pure-function ordering pins (paper_exit_decision) ───────────────────────────

def test_clamp_bounds_gap_only_friction_below(monkeypatch):
    """Gap target below the traded low -> gap clamped AT the low, slip+fee
    applied BELOW the clamp (pre-fix: booked the low exactly, friction erased)."""
    monkeypatch.setenv("GAP_THROUGH_HAIRCUT_PCT", "5.0")
    eb, why = paper_exit_decision(0.10, None, "hard_stop", "enforce", SIZE,
                                  slip_pct=1.5, fee_usd=FEE, low_price=0.097)
    expected = effective_fill(0.097, "sell", 1.5, FEE, SIZE)
    assert why == "fresh" and eb == pytest.approx(expected, rel=1e-12)
    assert eb < 0.097   # friction survives the clamp


def test_stale_low_above_decision_clamps_to_decision(monkeypatch):
    """low_price ABOVE the decision print (stale MAE) can never raise the
    basis above the decision price."""
    monkeypatch.setenv("GAP_THROUGH_HAIRCUT_PCT", "5.0")
    eb, _ = paper_exit_decision(0.10, None, "tp1", "enforce", SIZE,
                                slip_pct=1.5, fee_usd=FEE, low_price=0.12)
    expected = effective_fill(0.10, "sell", 1.5, FEE, SIZE)
    assert eb == pytest.approx(expected, rel=1e-12)


def test_deep_mae_full_gap_haircut_survives(monkeypatch):
    """True deep low (below the gap target) -> full gap haircut + friction,
    same as before the reorder (multiplication commutes)."""
    monkeypatch.setenv("GAP_THROUGH_HAIRCUT_PCT", "5.0")
    eb, _ = paper_exit_decision(0.10, None, "hard_stop", "enforce", SIZE,
                                slip_pct=1.5, fee_usd=FEE, low_price=0.09)
    expected = effective_fill(0.10 * 0.95, "sell", 1.5, FEE, SIZE)
    assert eb == pytest.approx(expected, rel=1e-12)
