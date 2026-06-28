# -*- coding: utf-8 -*-
"""CORPSE-EXIT watchdog (SHADOW-first forced-exit, 2026-06-28).

The entire exit pipeline is gated on a live price fetch in the decision loop
(`price = priced[pkey]; if price is None: continue`). A feed-dead / rugged /
no-route token returns None EVERY cycle, so pm.tick() never runs and even
time_stop / never_runner can't fire -> the bag is held to ~-100% (real example:
live BOB, 1 buy / 0 sell). _maybe_corpse_exit is the only path that can free
such a position.

CORPSE_EXIT_MODE=off|shadow|enforce (default off). Default off = byte-identical:
_maybe_corpse_exit returns IMMEDIATELY (no records, no sells, no state writes)."""
import asyncio
import os

import pytest

from types import SimpleNamespace as NS

from core.fast_watch import rt_mode
from feeds.dip_scanner import DipScanner


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---- flag resolver -------------------------------------------------------

@pytest.mark.parametrize("val,expected", [
    ("off", "off"), ("shadow", "shadow"), ("enforce", "enforce"),
    ("garbage", "off"), ("SHADOW", "shadow"), ("  Enforce ", "enforce"),
])
def test_flag_resolver(monkeypatch, val, expected):
    monkeypatch.setenv("CORPSE_EXIT_MODE", val)
    assert rt_mode("CORPSE_EXIT_MODE") == expected


def test_flag_resolver_default_off(monkeypatch):
    monkeypatch.delenv("CORPSE_EXIT_MODE", raising=False)
    assert rt_mode("CORPSE_EXIT_MODE") == "off"


# ---- scanner harness -----------------------------------------------------

def _scanner_with_position(entry_time=0.0, last_good_ts=None, last_price=0.5,
                           bot_id="badday_flush", addr="mintTOK",
                           has_route=True, remaining_fraction=1.0):
    """A DipScanner skeleton holding one position, with capturing stubs."""
    sc = DipScanner.__new__(DipScanner)
    sb = {}
    if last_good_ts is not None:
        sb["corpse_last_good_ts"] = last_good_ts
    if last_price is not None:
        sb["corpse_last_price"] = last_price
    pos = NS(token="TOK", address=addr, pair_address="pairTOK",
             entry_price=1.0, entry_time=entry_time,
             remaining_fraction=remaining_fraction, state_blob=sb)

    class _PM:
        def __init__(self):
            self.config = NS(bot_id=bot_id)
            self.closed = []

        def iter_positions(self):
            return [pos]

        def close_position(self, token, exit_price, exit_time, reason, sell_fraction):
            self.closed.append(NS(token=token, exit_price=exit_price,
                                  reason=reason, sell_fraction=sell_fraction))
            return NS(cost_usd=20.0, proceeds_usd=0.0, entry_price=1.0)

    pm = _PM()
    sc.bot_position_managers = {bot_id: pm}

    sc._corpse_shadow_recs = []

    def _append(rec):
        sc._corpse_shadow_recs.append(rec)
    sc._append_corpse_shadow = _append

    sc._sell_calls = []

    async def _sell(bot_id_, token, decision, price, now):
        sc._sell_calls.append((bot_id_, token, decision, price, now))
    sc._execute_bot_sell = _sell

    # route probe: a trader whose _get_quote returns a dict (route) or None.
    async def _get_quote(input_mint, output_mint, amount, slippage_bps=600):
        return {"outAmount": "123"} if has_route else None
    sc.trader = NS(_get_quote=_get_quote)

    # no-route paper-close plumbing
    sc.bot_capitals = {bot_id: NS(realize_sell=lambda cost_usd, proceeds_usd: None)}
    sc._record_calls = []

    class _TS:
        # mirror the REAL signature: record_trade_async(self, trade, bot_id)
        async def record_trade_async(self, rec, bot_id):
            sc._record_calls.append((rec, bot_id))
    sc.trade_store = _TS()
    return sc, pos, pm


# ---- default OFF = byte-identical no-op -----------------------------------

def test_off_is_noop(monkeypatch):
    monkeypatch.delenv("CORPSE_EXIT_MODE", raising=False)
    # very stale feed AND past max-hold -> would trigger if enabled
    sc, pos, pm = _scanner_with_position(entry_time=0.0, last_good_ts=0.0)
    before = dict(pos.state_blob)
    _run(sc._maybe_corpse_exit("badday_flush", pm, pos, now=10_000_000.0))
    assert sc._corpse_shadow_recs == []
    assert sc._sell_calls == []
    assert pm.closed == []
    assert sc._record_calls == []
    # no state mutation (no corpse_shadow_emitted flag written)
    assert pos.state_blob == before


# ---- stale-feed trigger (shadow) -----------------------------------------

def test_shadow_stale_feed_triggers_records_no_sell(monkeypatch):
    monkeypatch.setenv("CORPSE_EXIT_MODE", "shadow")
    monkeypatch.setenv("CORPSE_STALE_SECS", "900")
    now = 100_000.0
    # last good price 1000s ago (> 900) but hold well under max-hold
    sc, pos, pm = _scanner_with_position(entry_time=now - 1000.0,
                                         last_good_ts=now - 1000.0)
    _run(sc._maybe_corpse_exit("badday_flush", pm, pos, now=now))
    assert len(sc._corpse_shadow_recs) == 1
    rec = sc._corpse_shadow_recs[0]
    assert rec["reason"] == "corpse_exit_stale"
    assert rec["no_route"] is False           # route exists
    assert sc._sell_calls == []               # NEVER sells in shadow
    assert pm.closed == []
    assert pos.state_blob.get("corpse_shadow_emitted") is True


def test_shadow_emits_once_per_position(monkeypatch):
    monkeypatch.setenv("CORPSE_EXIT_MODE", "shadow")
    now = 100_000.0
    sc, pos, pm = _scanner_with_position(entry_time=now - 1000.0,
                                         last_good_ts=now - 1000.0)
    _run(sc._maybe_corpse_exit("badday_flush", pm, pos, now=now))
    _run(sc._maybe_corpse_exit("badday_flush", pm, pos, now=now + 60))
    assert len(sc._corpse_shadow_recs) == 1   # no per-cycle spam


# ---- max-hold backstop trigger -------------------------------------------

def test_shadow_max_hold_backstop_triggers(monkeypatch):
    monkeypatch.setenv("CORPSE_EXIT_MODE", "shadow")
    monkeypatch.setenv("CORPSE_STALE_SECS", "900")
    monkeypatch.setenv("CORPSE_MAX_HOLD_MIN", "240")
    now = 1_000_000.0
    # feed FRESH (last good 10s ago) but held 250 min > 240 backstop
    sc, pos, pm = _scanner_with_position(entry_time=now - 250 * 60,
                                         last_good_ts=now - 10.0)
    _run(sc._maybe_corpse_exit("badday_flush", pm, pos, now=now))
    assert len(sc._corpse_shadow_recs) == 1
    assert sc._corpse_shadow_recs[0]["reason"] == "corpse_exit_maxage"
    assert sc._sell_calls == []


# ---- fresh feed -> no trigger --------------------------------------------

def test_fresh_feed_no_trigger(monkeypatch):
    monkeypatch.setenv("CORPSE_EXIT_MODE", "shadow")
    monkeypatch.setenv("CORPSE_STALE_SECS", "900")
    monkeypatch.setenv("CORPSE_MAX_HOLD_MIN", "240")
    now = 100_000.0
    # last good 10s ago, held 5 min -> neither threshold hit
    sc, pos, pm = _scanner_with_position(entry_time=now - 300.0,
                                         last_good_ts=now - 10.0)
    _run(sc._maybe_corpse_exit("badday_flush", pm, pos, now=now))
    assert sc._corpse_shadow_recs == []
    assert sc._sell_calls == []
    assert pos.state_blob.get("corpse_shadow_emitted") is None


# ---- enforce: recoverable corpse routes a real exit ----------------------

def test_enforce_recoverable_routes_sell(monkeypatch):
    monkeypatch.setenv("CORPSE_EXIT_MODE", "enforce")
    now = 100_000.0
    sc, pos, pm = _scanner_with_position(entry_time=now - 1000.0,
                                         last_good_ts=now - 1000.0,
                                         last_price=0.5, has_route=True)
    _run(sc._maybe_corpse_exit("badday_flush", pm, pos, now=now))
    assert len(sc._sell_calls) == 1
    bot_id, token, decision, price, when = sc._sell_calls[0]
    assert decision.reason == "corpse_exit_stale"
    assert decision.sell_fraction == 1.0
    assert price == pytest.approx(0.5)        # last-known good price
    assert pm.closed == []                    # routed via _execute_bot_sell, not direct
    assert sc._record_calls == []


# ---- enforce: un-sellable corpse -> paper close, no real sell ------------

def test_enforce_no_route_paper_closes(monkeypatch):
    monkeypatch.setenv("CORPSE_EXIT_MODE", "enforce")
    now = 100_000.0
    sc, pos, pm = _scanner_with_position(entry_time=now - 1000.0,
                                         last_good_ts=now - 1000.0,
                                         last_price=0.5, has_route=False)
    _run(sc._maybe_corpse_exit("badday_flush", pm, pos, now=now))
    assert sc._sell_calls == []               # NEVER a real sell with no route
    assert len(pm.closed) == 1
    assert pm.closed[0].reason == "corpse_no_route"
    assert pm.closed[0].exit_price == pytest.approx(0.5)
    assert len(sc._record_calls) == 1
    rec, rec_bot_id = sc._record_calls[0]
    assert rec_bot_id == "badday_flush"        # required bot_id arg is passed
    assert rec["no_route"] is True
    assert rec["corpse"] is True
    assert rec["paper_close_no_fill"] is True
    assert rec["reason"] == "corpse_no_route"


# ---- route probe fail-open (no trader -> assume reachable) ----------------

def test_route_probe_failopen_no_trader(monkeypatch):
    monkeypatch.setenv("CORPSE_EXIT_MODE", "shadow")
    now = 100_000.0
    sc, pos, pm = _scanner_with_position(entry_time=now - 1000.0,
                                         last_good_ts=now - 1000.0)
    sc.trader = None
    _run(sc._maybe_corpse_exit("badday_flush", pm, pos, now=now))
    assert len(sc._corpse_shadow_recs) == 1
    assert sc._corpse_shadow_recs[0]["no_route"] is False   # fail-open -> reachable
