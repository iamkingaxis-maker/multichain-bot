# -*- coding: utf-8 -*-
"""BUY-REPRICE guard (money leak, 2026-06-17).

A stale decision mid can make us buy a token that already ran UP off the dip we
decided on — paying away the edge. Before the live swap fires, fetch a FRESH price
and ASYMMETRICALLY abort: a RUN-UP past BUY_REPRICE_MAX_RUNUP (default +5%) aborts
(enforce) / logs (shadow); a further DIP is the edge and NEVER aborts. Missing
fresh price fails OPEN (allow). Behind BUY_REPRICE_MODE=off|shadow|enforce.

These tests drive the real DipScanner._execute_bot_buy_live through the reprice gate
with the swap + downstream calls stubbed, asserting whether the swap actually fired."""
import asyncio
import os

import pytest

from types import SimpleNamespace as NS
from feeds.dip_scanner import DipScanner


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeTrader:
    """Records whether the live swap fired; produces a clean trackable fill."""

    def __init__(self):
        self.swap_called = False
        self.last_lamports = None

    async def _usd_to_sol(self, usd):
        return usd / 100.0  # $100/SOL, arbitrary

    async def _check_sol_reserve(self, token):
        return True

    async def _get_token_balance_atomic(self, mint):
        return 0

    async def _execute_swap_ultra(self, src, dst, lamports, slippage_bps=400):
        self.swap_called = True
        self.last_lamports = lamports
        return {"success": True, "out_amount": 1_000_000, "route": "test",
                "signature": "SIG", "realized_slippage_pct": 0.1}

    async def _get_token_decimals(self, mint):
        return 6


class _FakePM:
    def __init__(self):
        self.config = NS(max_concurrent_positions=3)
        self.open_count = 0
        self.opened_entry = None

    def get_position(self, token):
        return None

    def open_position(self, token, entry_price, size_usd, entry_time,
                      address=None, pair_address=None):
        self.opened_entry = entry_price
        return NS(token=token, entry_price=entry_price, size_usd=size_usd,
                  state_blob={}, entry_time=entry_time,
                  address=address, pair_address=pair_address)


def _scanner(fresh_price):
    sc = DipScanner.__new__(DipScanner)
    sc.trader = _FakeTrader()
    sc.pool_price_feed = None

    async def _fake_fresh(token, address="", pair_address=""):
        return fresh_price

    sc._get_current_price_for = _fake_fresh
    # _recent_low_sync is used after the swap; keep it simple.
    sc._recent_low_sync = lambda pair: None
    return sc


def _decision(mid):
    return NS(token="TOK", address="mintTOK", pair_address="pairTOK",
              entry_price=mid, local_low=None)


def _set_mode(monkeypatch, mode, runup=None):
    monkeypatch.setenv("BUY_REPRICE_MODE", mode)
    if runup is not None:
        monkeypatch.setenv("BUY_REPRICE_MAX_RUNUP", str(runup))
    else:
        monkeypatch.delenv("BUY_REPRICE_MAX_RUNUP", raising=False)
    # keep slip cap deterministic
    monkeypatch.setenv("PROBE_ULTRA_SLIPPAGE_BPS", "400")


# ---- ENFORCE: run-up past threshold aborts -------------------------------

def test_enforce_runup_aborts(monkeypatch):
    _set_mode(monkeypatch, "enforce")
    sc = _scanner(fresh_price=1.10)        # +10% vs mid 1.0 > 5%
    r = _run(sc._execute_bot_buy_live(_decision(1.0), _FakePM(), 30.0))
    assert r is None                       # aborted -> caller refunds
    assert sc.trader.swap_called is False   # swap NEVER fired


def test_enforce_runup_just_over_aborts(monkeypatch):
    _set_mode(monkeypatch, "enforce", runup=0.05)
    sc = _scanner(fresh_price=1.06)        # +6% > 5%
    r = _run(sc._execute_bot_buy_live(_decision(1.0), _FakePM(), 30.0))
    assert r is None
    assert sc.trader.swap_called is False


# ---- ENFORCE: a DIP never aborts (the edge), and rebases entry ------------

def test_enforce_dip_never_aborts(monkeypatch):
    _set_mode(monkeypatch, "enforce")
    sc = _scanner(fresh_price=0.80)        # -20% deeper dip = the edge
    pm = _FakePM()
    r = _run(sc._execute_bot_buy_live(_decision(1.0), pm, 30.0))
    assert r is not None                   # allowed
    assert sc.trader.swap_called is True    # swap fired


def test_enforce_subthreshold_runup_allowed(monkeypatch):
    _set_mode(monkeypatch, "enforce")
    sc = _scanner(fresh_price=1.03)        # +3% < 5% -> allowed
    r = _run(sc._execute_bot_buy_live(_decision(1.0), _FakePM(), 30.0))
    assert r is not None
    assert sc.trader.swap_called is True


def test_enforce_allowed_uses_fresh_as_basis(monkeypatch):
    # In enforce, an ALLOWED buy uses the fresh price as the recorded entry basis.
    # The fill is implausible-suspect vs the (rebased) mid only if >3x off; here the
    # fake fill out_tokens=1.0 (1e6/1e6) -> real_entry = size/out = 30.0, which is far
    # from any sane mid, so M10 forces entry := mid. We assert that forced basis is the
    # FRESH price, not the stale decision mid.
    _set_mode(monkeypatch, "enforce")
    sc = _scanner(fresh_price=0.90)        # dip -> allowed, rebases mid to 0.90
    pm = _FakePM()
    r = _run(sc._execute_bot_buy_live(_decision(1.0), pm, 30.0))
    assert r is not None
    # M10 suspect-fallback records entry = the (fresh-rebased) mid, not stale 1.0
    assert pm.opened_entry == pytest.approx(0.90)


# ---- SHADOW: never aborts (just logs) ------------------------------------

def test_shadow_runup_never_aborts(monkeypatch):
    _set_mode(monkeypatch, "shadow")
    sc = _scanner(fresh_price=1.50)        # +50% would-abort, but shadow only logs
    r = _run(sc._execute_bot_buy_live(_decision(1.0), _FakePM(), 30.0))
    assert r is not None
    assert sc.trader.swap_called is True


def test_shadow_does_not_rebase_entry(monkeypatch):
    # Shadow must be byte-identical behavior except for the log line -> no rebase.
    _set_mode(monkeypatch, "shadow")
    sc = _scanner(fresh_price=0.90)        # a dip; shadow must NOT touch the basis
    pm = _FakePM()
    r = _run(sc._execute_bot_buy_live(_decision(1.0), pm, 30.0))
    assert r is not None
    assert pm.opened_entry == pytest.approx(1.0)   # original mid preserved


# ---- OFF: completely inert -----------------------------------------------

def test_off_runup_never_aborts(monkeypatch):
    _set_mode(monkeypatch, "off")
    sc = _scanner(fresh_price=2.00)        # +100% ignored entirely
    pm = _FakePM()
    r = _run(sc._execute_bot_buy_live(_decision(1.0), pm, 30.0))
    assert r is not None
    assert sc.trader.swap_called is True
    assert pm.opened_entry == pytest.approx(1.0)


# ---- MISSING fresh price: fails OPEN (allow) -----------------------------

def test_enforce_missing_fresh_fails_open(monkeypatch):
    _set_mode(monkeypatch, "enforce")
    sc = _scanner(fresh_price=None)        # feed gap -> must NOT block
    r = _run(sc._execute_bot_buy_live(_decision(1.0), _FakePM(), 30.0))
    assert r is not None
    assert sc.trader.swap_called is True


def test_enforce_zero_fresh_fails_open(monkeypatch):
    _set_mode(monkeypatch, "enforce")
    sc = _scanner(fresh_price=0.0)         # bad price <=0 -> fail open
    r = _run(sc._execute_bot_buy_live(_decision(1.0), _FakePM(), 30.0))
    assert r is not None
    assert sc.trader.swap_called is True
