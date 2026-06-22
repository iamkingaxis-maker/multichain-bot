# -*- coding: utf-8 -*-
"""ENFORCE-GATE behavioral integration test for PAPER↔LIVE 1:1 fidelity.

The pure helpers in core/paper_fidelity.py are unit-tested in
tests/test_paper_fidelity.py. This file drives the REAL dip_scanner paper
buy + sell wiring end-to-end and asserts the BOOKED entry/exit price (and the
skip/position outcome) under PAPER_FIDELITY_MODE off/enforce — i.e. that the
gate actually changes the recorded fill, not just a log line.

BUY  -> DipScanner._execute_bot_buy  (paper _live-False branch, ~ds.py:1750-1875)
SELL -> DipScanner._execute_bot_sell (paper sell branch,        ~ds.py:3104-3145)

Both methods are driven for real (no re-implementation): we construct the
scanner via __new__, wire the minimal real collaborators (PerBotCapital,
PerBotPositionManager, BotConfig), stub the price-source seams
(_get_current_price_for / _fast_price_for) and call the actual coroutine. The
assertions compare the booked price against the canonical slippage_model /
paper_fidelity math, so the test FAILS if the gate logic regresses.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace as NS

from feeds.dip_scanner import DipScanner
from core.bot_config import BotConfig
from core.per_bot_capital import PerBotCapital
from core.per_bot_position_manager import PerBotPositionManager, ExitDecision
from core.slippage_model import buy_fill_price, sell_fill_price
from core.paper_fidelity import (
    effective_fill,
    measured_live_slip_pct,
    paper_fee_usd,
    gap_through_extra_pct,
)

BOT_ID = "fid_test_bot"
ADDR = "MintAaaBbbCccDddEeeFffGggHhhIiiJjjKkkLpump"  # mixed-case, base58-ish


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Bundle:
    """Minimal BuyBundle: only raw_meta / mcap_usd / liquidity_usd are read by
    the buy gates we traverse. liquidity_usd is set ABOVE the anti-rug floor so
    we reach the paper-fill branch (not blocked at the floor)."""

    def __init__(self):
        self.raw_meta = {}
        # >= scanner min_mcap (1M) so the badday-lane / low-mcap gates treat this
        # as a normal-cap token (not a sub-floor microcap requiring a mandate).
        self.mcap_usd = 2_000_000.0
        self.liquidity_usd = 50_000.0     # above the anti-rug floor (25k)
        self.pc_h1 = 0.0
        self.shape_90m_drawdown_from_max_pct = None


def _decision(mid, size_usd=30.0):
    return NS(
        bot_id=BOT_ID,
        token="TOK",
        address=ADDR,
        pair_address="pairTOK",
        entry_price=mid,
        size_usd=size_usd,
        size_tier="base",
        triggers_fired=["test_trigger"],
    )


def _make_scanner(fresh_price, fresh_source="jupiter"):
    """Build a DipScanner shell with the real per-bot collaborators wired and the
    price-source seams stubbed. Empty private key => paper route (no live cap)."""
    sc = DipScanner.__new__(DipScanner)

    cfg = BotConfig(bot_id=BOT_ID, display_name="fidelity test")
    pm = PerBotPositionManager(cfg)
    cap = PerBotCapital(bot_id=BOT_ID, starting_balance_usd=2000.0)

    sc.bot_capitals = {BOT_ID: cap}
    sc.bot_position_managers = {BOT_ID: pm}

    sc.trader = NS(private_key="")          # paper route
    sc._addr_by_token = {}
    sc._fast_armed = {}                     # nothing armed -> no-fast-price gate inert
    sc._buy_gate = None
    sc._token_registry = None
    sc._exit_price_guard = {}
    sc._cycle_sol_features = {}
    sc.min_mcap = 1_000_000
    sc._user_watchlist_addrs = set()
    sc.trade_store = None                   # skip the ledger write
    sc.pool_price_feed = None
    sc.open_positions_ref = {}

    async def _fake_fresh(token, address="", pair_address=""):
        return fresh_price

    sc._get_current_price_for = _fake_fresh
    sc._fast_price_for = lambda addr, jup: (jup, fresh_source)
    # _log_fill_speed_record + _holder_features_cached must be inert no-ops.
    sc._log_fill_speed_record = lambda *a, **k: None

    async def _no_holders(*a, **k):
        return {}

    sc._holder_features_cached = _no_holders
    sc._sol_flk_1h = lambda now=None: 0
    return sc, pm, cap


def _set_buy_env(monkeypatch, mode, runup="0.05"):
    monkeypatch.setenv("PAPER_FIDELITY_MODE", mode)
    monkeypatch.setenv("BUY_REPRICE_MAX_RUNUP", runup)
    # Keep the modeled slippage well below the cap so slippage_cap never fires.
    monkeypatch.setenv("PROBE_ULTRA_SLIPPAGE_BPS", "400")
    monkeypatch.setenv("PAPER_LIVE_SLIP_PCT", "1.5")
    monkeypatch.setenv("PAPER_FEE_USD_PER_TX", "0.17")
    # Neutralize gates that could short-circuit the buy before the paper branch.
    monkeypatch.setenv("NO_FAST_PRICE_GATE_MODE", "off")
    monkeypatch.setenv("PAPER_PER_TOKEN_CAP_MODE", "off")
    monkeypatch.setenv("ANTIRUG_FLOOR_MODE", "shadow")
    monkeypatch.setenv("RISK_FLOOR_MODE", "shadow")
    monkeypatch.setenv("NEGGATE_MODE", "shadow")


# =========================================================================
# BUY
# =========================================================================

def test_buy_enforce_reprices_to_fresh_above_stale(monkeypatch):
    """enforce + fresh ABOVE stale (within max_runup) => booked entry is the
    fresh-repriced + slipped value, NOT the stale decision mid path."""
    _set_buy_env(monkeypatch, "enforce", runup="0.05")
    stale, fresh = 1.00, 1.03           # +3% run-up, within +5% cap
    sc, pm, cap = _make_scanner(fresh_price=fresh)
    _run(sc._execute_bot_buy(_decision(stale, size_usd=30.0), _Bundle()))

    pos = pm.get_position("TOK")
    assert pos is not None, "enforce within-runup buy must OPEN a position"
    expected = effective_fill(
        fresh, "buy", measured_live_slip_pct(), paper_fee_usd(), 30.0
    )
    assert pos.entry_price == pytest.approx(expected)
    # Distinct from the stale-mid path the OFF mode would book.
    stale_path = buy_fill_price(stale, 30.0, {})[0]
    assert pos.entry_price != pytest.approx(stale_path)
    # Booked above the stale mid (we paid up for the run-up + slippage).
    assert pos.entry_price > stale


def test_buy_enforce_runup_past_max_skips(monkeypatch):
    """enforce + fresh run-up PAST BUY_REPRICE_MAX_RUNUP => paper buy SKIPPED,
    no position, capital reservation refunded."""
    _set_buy_env(monkeypatch, "enforce", runup="0.05")
    stale, fresh = 1.00, 1.20           # +20% > +5% cap => runup_abort
    sc, pm, cap = _make_scanner(fresh_price=fresh)
    start_bal = cap.balance_usd
    _run(sc._execute_bot_buy(_decision(stale, size_usd=30.0), _Bundle()))

    assert pm.get_position("TOK") is None, "run-up past max must SKIP the buy"
    assert cap.balance_usd == pytest.approx(start_bal), "capital must be refunded"
    assert cap.in_flight_usd == pytest.approx(0.0)


def test_buy_enforce_no_route_skips(monkeypatch):
    """enforce + no fresh route (source 'none') => SKIPPED (no_route)."""
    _set_buy_env(monkeypatch, "enforce")
    sc, pm, cap = _make_scanner(fresh_price=1.00, fresh_source="none")
    start_bal = cap.balance_usd
    _run(sc._execute_bot_buy(_decision(1.00, size_usd=30.0), _Bundle()))

    assert pm.get_position("TOK") is None, "no_route must SKIP the buy"
    assert cap.balance_usd == pytest.approx(start_bal), "capital must be refunded"
    assert cap.in_flight_usd == pytest.approx(0.0)


def test_buy_off_books_original_slippage_path(monkeypatch):
    """off => booked entry == the original buy_fill_price(decision.entry_price)
    path, byte-identical (the fresh price is never consulted)."""
    _set_buy_env(monkeypatch, "off")
    stale = 1.00
    # Fresh wildly different to prove OFF ignores it entirely.
    sc, pm, cap = _make_scanner(fresh_price=5.00)
    _run(sc._execute_bot_buy(_decision(stale, size_usd=30.0), _Bundle()))

    pos = pm.get_position("TOK")
    assert pos is not None
    expected = buy_fill_price(stale, 30.0, {})[0]
    assert pos.entry_price == pytest.approx(expected)


# =========================================================================
# SELL
# =========================================================================

def _open_for_sell(sc, pm, entry=1.00, size=30.0):
    """Seat an open position so the sell path has something to close."""
    import time as _t
    pos = pm.open_position(
        token="TOK", entry_price=entry, size_usd=size,
        entry_time=_t.time(), address=ADDR, pair_address="pairTOK",
    )
    pos.state_blob["slip_pct"] = 1.0
    return pos


def _set_sell_env(monkeypatch, mode):
    monkeypatch.setenv("PAPER_FIDELITY_MODE", mode)
    monkeypatch.setenv("PAPER_LIVE_SLIP_PCT", "1.5")
    monkeypatch.setenv("PAPER_FEE_USD_PER_TX", "0.17")
    monkeypatch.setenv("GAP_THROUGH_HAIRCUT_PCT", "5.0")


def test_sell_enforce_hard_stop_applies_gap_haircut(monkeypatch):
    """enforce + HARD_STOP => booked exit reflects fresh-reprice + slippage +
    gap-through haircut, and is LOWER than the plain sell_fill_price."""
    _set_sell_env(monkeypatch, "enforce")
    sc, pm, cap = _make_scanner(fresh_price=0.80)   # fresh exit mid
    pos = _open_for_sell(sc, pm)
    current = 0.80
    sold_usd = pos.size_usd                          # full close => sold slice = size
    ed = ExitDecision(token="TOK", kind="HARD_STOP",
                      reason="hard_stop", sell_fraction=1.0)

    _run(sc._execute_bot_sell(BOT_ID, "TOK", ed, current, _now()))
    base = effective_fill(0.80, "sell", measured_live_slip_pct(), paper_fee_usd(), sold_usd)
    expected = base * (1.0 - gap_through_extra_pct("hard_stop") / 100.0)
    plain = sell_fill_price(current, sold_usd, 1.0)
    # close_position consumed the position; assert via realized proceeds.
    # cost = size * ratio where ratio = exit/entry. Recover exit from cap delta.
    booked_exit = _recover_exit_price(cap, entry=1.00, size=sold_usd)
    assert booked_exit == pytest.approx(expected, rel=1e-6)
    assert booked_exit < plain, "gap haircut must make the stop fill WORSE than plain"
    assert gap_through_extra_pct("hard_stop") == pytest.approx(5.0)


def test_sell_enforce_tp1_no_gap_haircut(monkeypatch):
    """enforce + TP1 => fresh-reprice + slippage but NO gap haircut."""
    _set_sell_env(monkeypatch, "enforce")
    sc, pm, cap = _make_scanner(fresh_price=1.20)
    pos = _open_for_sell(sc, pm)
    current = 1.20
    sold_usd = pos.size_usd
    ed = ExitDecision(token="TOK", kind="TP1", reason="tp1", sell_fraction=1.0)

    _run(sc._execute_bot_sell(BOT_ID, "TOK", ed, current, _now()))
    expected = effective_fill(1.20, "sell", measured_live_slip_pct(), paper_fee_usd(), sold_usd)
    booked_exit = _recover_exit_price(cap, entry=1.00, size=sold_usd)
    assert booked_exit == pytest.approx(expected, rel=1e-6)
    # No haircut applied for a TP reason.
    assert gap_through_extra_pct("tp1") == pytest.approx(0.0)


def test_sell_off_books_original_sell_fill(monkeypatch):
    """off => booked exit == original sell_fill_price(...), byte-identical
    (fresh price never consulted)."""
    _set_sell_env(monkeypatch, "off")
    # Fresh wildly different to prove OFF ignores it.
    sc, pm, cap = _make_scanner(fresh_price=9.99)
    pos = _open_for_sell(sc, pm)
    current = 0.90
    sold_usd = pos.size_usd
    ed = ExitDecision(token="TOK", kind="HARD_STOP",
                      reason="hard_stop", sell_fraction=1.0)

    _run(sc._execute_bot_sell(BOT_ID, "TOK", ed, current, _now()))
    expected = sell_fill_price(current, sold_usd, 1.0)   # impact_pct stashed = 1.0
    booked_exit = _recover_exit_price(cap, entry=1.00, size=sold_usd)
    assert booked_exit == pytest.approx(expected, rel=1e-6)


# ---- helpers -------------------------------------------------------------

def _now():
    import time as _t
    return _t.time()


def _recover_exit_price(cap, entry, size):
    """Recover the booked exit PRICE from the realized capital delta.

    The position is seated directly via pm.open_position (no capital
    reservation), so balance stays at the 2000 start until the sell. On close,
    realize_sell credits balance by proceeds = size * (exit/entry). So:
        proceeds  = balance - 2000
        exit_price = (proceeds / size) * entry
    """
    proceeds = cap.balance_usd - 2000.0
    ratio = proceeds / size
    return ratio * entry
