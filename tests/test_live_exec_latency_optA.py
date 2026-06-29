# -*- coding: utf-8 -*-
"""Option A — cut OUR OWN pre-swap serial overhead on the LIVE buy path (latency).

The live buy route is Jupiter Ultra; the only latency we control is ~0.5-1.2s of
our own serial pre-swap I/O before the swap. These tests pin down two surgical,
flag-gated, fail-open changes WITHOUT altering fill correctness:

  A1 (LIVE_POSTSWAP_COST_RECON, default on): the forced un-cached
     _get_sol_balance(force=True) cost-reconcile "before" read that runs RIGHT
     BEFORE the swap is moved off the hot path — the "before" snapshot is taken
     from the cached balance the pre-swap reserve check already fetched, and the
     forced read stays only on the post-swap "after" side. Kill (=off) restores
     the original pre-swap forced read.

  A2 (BUY_REPRICE_MODE gate): the BUY-REPRICE fresh-price fetch on the fire path
     runs ONLY in enforce mode. In shadow/off it must NOT do the extra network
     fetch. Enforce behavior stays byte-identical.

A3 (parallelize the enforce reprice into the gather) was deliberately SKIPPED —
see live_exec_optA_report.md for the rationale (it would change enforce-mode
side effects / require orphan-task cleanup for a ~200ms win in a non-default mode).

These drive the REAL DipScanner._execute_bot_buy_live with the swap + telemetry
stubbed; no network. PAPER_MODE stays irrelevant (the live coroutine is called
directly with a stubbed trader)."""
import asyncio

import pytest

from types import SimpleNamespace as NS

import core.live_swap_log as live_swap_log
from feeds.dip_scanner import DipScanner


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _SpyTrader:
    """Records the ORDER of forced-balance reads vs the swap, and simulates the
    cached-balance population that _check_sol_reserve performs in real life."""

    def __init__(self, forced_values=None, raise_on_forced=False, cached_value=10.0):
        self.events = []                 # ordered (kind, detail) log
        self._sol_balance = -1.0         # the Trader cache _check_sol_reserve fills
        self._forced = list(forced_values or [])   # values for force=True reads, FIFO
        self._raise_on_forced = raise_on_forced
        self._cached_value = cached_value          # value the non-forced read installs
        self.swap_called = False

    async def _usd_to_sol(self, usd):
        return usd / 100.0  # $100/SOL

    async def _get_sol_balance(self, force=False):
        self.events.append(("balance", bool(force)))
        if force and self._raise_on_forced:
            raise RuntimeError("simulated getBalance RPC failure")
        if force:
            val = self._forced.pop(0) if self._forced else 9.0
        else:
            val = self._cached_value
        if val is not None and val >= 0:
            self._sol_balance = val   # mirror real cache write
        return val

    async def _check_sol_reserve(self, token):
        # Real _check_sol_reserve calls the non-forced _get_sol_balance (populates cache).
        await self._get_sol_balance()
        return True

    async def _get_token_balance_atomic(self, mint):
        return 0

    async def _execute_swap_ultra(self, src, dst, lamports, slippage_bps=400, buy_context=False):
        self.swap_called = True
        self.events.append(("swap", None))
        return {"success": True, "out_amount": 1_000_000, "route": "test",
                "signature": "SIG", "realized_slippage_pct": 0.1}

    async def _get_token_decimals(self, mint):
        return 6


class _FakePM:
    def __init__(self):
        self.config = NS(max_concurrent_positions=3)
        self.open_count = 0
        self.opened_entry = None
        self.opened = False

    def get_position(self, token):
        return None

    def open_position(self, token, entry_price, size_usd, entry_time,
                      address=None, pair_address=None):
        self.opened = True
        self.opened_entry = entry_price
        return NS(token=token, entry_price=entry_price, size_usd=size_usd,
                  state_blob={}, entry_time=entry_time,
                  address=address, pair_address=pair_address)


class _PriceSpy:
    def __init__(self, fresh_price):
        self.calls = 0
        self._fresh = fresh_price

    async def __call__(self, token, address="", pair_address=""):
        self.calls += 1
        return self._fresh


def _scanner(trader, price_spy=None):
    sc = DipScanner.__new__(DipScanner)
    sc.trader = trader
    sc.pool_price_feed = None
    if price_spy is None:
        async def _no_fetch(token, address="", pair_address=""):
            raise AssertionError("_get_current_price_for must not be called here")
        sc._get_current_price_for = _no_fetch
    else:
        sc._get_current_price_for = price_spy
    sc._recent_low_sync = lambda pair: None
    return sc


def _decision(mid=1.0):
    return NS(token="TOK", address="mintTOK", pair_address="pairTOK",
              entry_price=mid, local_low=None)


def _capture_telemetry(monkeypatch):
    cap = {}

    def _spy_log(**kwargs):
        cap.update(kwargs)

    monkeypatch.setattr(live_swap_log, "log_live_swap", _spy_log)
    return cap


def _first_forced_idx(events):
    for i, (kind, force) in enumerate(events):
        if kind == "balance" and force:
            return i
    return None


def _swap_idx(events):
    for i, (kind, _) in enumerate(events):
        if kind == "swap":
            return i
    return None


# ===================== A1 =====================

def test_A1_cost_recon_balance_read_after_swap(monkeypatch):
    """Default (on): NO forced getBalance before the swap; forced read happens
    AFTER; cost-reconcile (sol_before/after/spent) still computed correctly."""
    monkeypatch.setenv("BUY_REPRICE_MODE", "off")
    monkeypatch.setenv("LIVE_POSTSWAP_COST_RECON", "on")
    cap = _capture_telemetry(monkeypatch)
    # cached reserve read installs 10.0 (the "before"); post-swap forced read = 9.5.
    tr = _SpyTrader(forced_values=[9.5], cached_value=10.0)
    pm = _FakePM()
    r = _run(_scanner(tr)._execute_bot_buy_live(_decision(1.0), pm, 30.0))

    assert r is not None and "pos" in r
    swap_i = _swap_idx(tr.events)
    forced_i = _first_forced_idx(tr.events)
    assert swap_i is not None
    # no forced read before the swap; the only forced read is post-swap
    assert forced_i is not None and forced_i > swap_i
    # reconcile math intact: before = cached 10.0, after = forced 9.5, spent = 0.5
    assert cap.get("sol_before") == pytest.approx(10.0)
    assert cap.get("sol_after") == pytest.approx(9.5)
    assert cap.get("sol_spent") == pytest.approx(0.5)


def test_A1_kill_switch_restores_preswap_forced_read(monkeypatch):
    """Kill (off): a forced getBalance occurs BEFORE the swap (original order)."""
    monkeypatch.setenv("BUY_REPRICE_MODE", "off")
    monkeypatch.setenv("LIVE_POSTSWAP_COST_RECON", "off")
    cap = _capture_telemetry(monkeypatch)
    # two forced reads now: pre-swap "before" = 10.0, post-swap "after" = 9.5
    tr = _SpyTrader(forced_values=[10.0, 9.5], cached_value=7.0)
    pm = _FakePM()
    r = _run(_scanner(tr)._execute_bot_buy_live(_decision(1.0), pm, 30.0))

    assert r is not None and "pos" in r
    swap_i = _swap_idx(tr.events)
    forced_i = _first_forced_idx(tr.events)
    assert forced_i is not None and swap_i is not None
    assert forced_i < swap_i          # forced "before" read precedes the swap
    assert cap.get("sol_before") == pytest.approx(10.0)
    assert cap.get("sol_after") == pytest.approx(9.5)
    assert cap.get("sol_spent") == pytest.approx(0.5)


def test_A1_failopen(monkeypatch):
    """Post-swap reconcile read raising must NOT block the fill: swap result
    returned, position opened, no exception, sol_after telemetry = None."""
    monkeypatch.setenv("BUY_REPRICE_MODE", "off")
    monkeypatch.setenv("LIVE_POSTSWAP_COST_RECON", "on")
    cap = _capture_telemetry(monkeypatch)
    tr = _SpyTrader(forced_values=[], raise_on_forced=True, cached_value=10.0)
    pm = _FakePM()
    r = _run(_scanner(tr)._execute_bot_buy_live(_decision(1.0), pm, 30.0))

    assert r is not None and "pos" in r
    assert pm.opened is True
    assert tr.swap_called is True
    assert cap.get("sol_after") is None   # forced post-swap read failed -> None


# ===================== A2 =====================

def test_A2_reprice_skipped_when_not_enforce(monkeypatch):
    """shadow -> the BUY-REPRICE fresh fetch is NOT performed on the fire path;
    enforce -> it IS performed and still gates (run-up aborts)."""
    monkeypatch.setenv("LIVE_POSTSWAP_COST_RECON", "on")

    # shadow: fetch must NOT run (even though a +50% run-up price is available)
    monkeypatch.setenv("BUY_REPRICE_MODE", "shadow")
    spy_shadow = _PriceSpy(fresh_price=1.50)
    tr_s = _SpyTrader(forced_values=[9.5], cached_value=10.0)
    r_s = _run(_scanner(tr_s, spy_shadow)._execute_bot_buy_live(_decision(1.0), _FakePM(), 30.0))
    assert r_s is not None
    assert spy_shadow.calls == 0          # no extra network fetch in shadow
    assert tr_s.swap_called is True

    # enforce: fetch runs and still gates -> +10% run-up aborts the swap
    monkeypatch.setenv("BUY_REPRICE_MODE", "enforce")
    spy_enforce = _PriceSpy(fresh_price=1.10)
    tr_e = _SpyTrader(forced_values=[9.5], cached_value=10.0)
    r_e = _run(_scanner(tr_e, spy_enforce)._execute_bot_buy_live(_decision(1.0), _FakePM(), 30.0))
    assert spy_enforce.calls == 1         # enforce DOES fetch
    assert r_e is None                    # run-up aborted
    assert tr_e.swap_called is False


def test_A2_enforce_unchanged(monkeypatch):
    """Enforce fill decision byte-identical to pre-change: a dip rebases the
    recorded entry basis to the fresh price (M10 suspect-fallback uses fresh mid)."""
    monkeypatch.setenv("LIVE_POSTSWAP_COST_RECON", "on")
    monkeypatch.setenv("BUY_REPRICE_MODE", "enforce")
    spy = _PriceSpy(fresh_price=0.90)     # a dip -> allowed, rebases mid to 0.90
    tr = _SpyTrader(forced_values=[9.5], cached_value=10.0)
    pm = _FakePM()
    r = _run(_scanner(tr, spy)._execute_bot_buy_live(_decision(1.0), pm, 30.0))
    assert r is not None
    assert spy.calls == 1
    assert tr.swap_called is True
    assert pm.opened_entry == pytest.approx(0.90)   # rebased to fresh, not stale 1.0
