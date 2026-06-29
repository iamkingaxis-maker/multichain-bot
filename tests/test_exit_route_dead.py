# -*- coding: utf-8 -*-
"""EXIT-ROUTE-DEAD execution-fidelity gate (SHADOW-first, EXIT_ROUTE_DEAD_MODE).

Confirmed gap #2 (subsumes #3) of the sell-leg fidelity audit: the PAPER twin
books a clean FULL-close exit on a token whose LIVE sell route is DEAD (no
Jupiter route / honeypot / transfer-tax / route-revert). Paper credits an exit
a funded live bot could NOT execute -> OVERSTATES live P&L by ~the whole
position.

CORPSE_EXIT_MODE only catches FEED-DEAD tokens (price is None branch); this gate
fires on tokens that STILL PRINT a price (corpse never runs) but whose live SELL
route is dead. Fires ONLY on a FULL-close paper exit (sell_fraction >= 1.0), in
the PAPER branch.

EXIT_ROUTE_DEAD_MODE=off|shadow|enforce (default off). Default off =
byte-identical: the gate short-circuits BEFORE any route probe / compute / log
and returns False (caller books the normal exit)."""
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


def _scanner(bot_id="badday_flush", addr="mintTOK", entry_price=1.0,
             remaining_fraction=1.0):
    """A DipScanner skeleton + one position with capturing stubs for the gate."""
    sc = DipScanner.__new__(DipScanner)
    pos = NS(token="TOK", address=addr, pair_address="pairTOK",
             entry_price=entry_price, entry_time=0.0,
             remaining_fraction=remaining_fraction, state_blob={})

    class _PM:
        def __init__(self):
            self.config = NS(bot_id=bot_id)
            self.closed = []

        def close_position(self, token, exit_price, exit_time, reason, sell_fraction):
            self.closed.append(NS(token=token, exit_price=exit_price,
                                  reason=reason, sell_fraction=sell_fraction))
            return NS(cost_usd=20.0, proceeds_usd=0.0, entry_price=entry_price)

    pm = _PM()
    sc.bot_position_managers = {bot_id: pm}
    sc._addr_by_token = {"TOK": addr}

    # shadow JSONL sink (capture instead of writing to disk)
    sc._erd_recs = []
    sc._append_exit_route_dead_shadow = lambda rec: sc._erd_recs.append(rec)

    # route-probe spy (records calls; behavior set per-test)
    sc._probe_calls = []
    sc.bot_capitals = {bot_id: NS(realize_sell=lambda cost_usd, proceeds_usd: None)}
    sc._record_calls = []

    class _TS:
        async def record_trade_async(self, rec, bot_id):
            sc._record_calls.append((rec, bot_id))
    sc.trade_store = _TS()
    return sc, pos, pm


def _set_route(sc, alive=True, raises=False):
    async def _probe(position):
        sc._probe_calls.append(position)
        if raises:
            raise RuntimeError("quote timeout")
        return alive
    sc._corpse_has_route = _probe


# ---- 1) off = byte-identical: no probe, no log, normal exit -----------------

def test_off_no_probe_no_log(monkeypatch):
    monkeypatch.delenv("EXIT_ROUTE_DEAD_MODE", raising=False)
    sc, pos, pm = _scanner()
    _set_route(sc, alive=False)  # route dead, but OFF must not even probe
    preempt = _run(sc._maybe_exit_route_dead(
        "badday_flush", pm, "TOK", pos,
        NS(reason="hard_stop"), 1.0, eff_exit=0.8, current_price=0.8, now=1.0))
    assert preempt is False                 # caller books the normal exit
    assert sc._probe_calls == []            # short-circuit BEFORE the probe
    assert sc._erd_recs == []               # no file written
    assert pm.closed == []                  # gate did not book anything itself


# ---- 2) shadow + route DEAD -> log, book normal (unchanged) -----------------

def test_shadow_dead_logs_books_normal(monkeypatch):
    monkeypatch.setenv("EXIT_ROUTE_DEAD_MODE", "shadow")
    sc, pos, pm = _scanner(entry_price=1.0)
    _set_route(sc, alive=False)
    preempt = _run(sc._maybe_exit_route_dead(
        "badday_flush", pm, "TOK", pos,
        NS(reason="hard_stop"), 1.0, eff_exit=0.8, current_price=0.8, now=42.0))
    assert preempt is False                 # SHADOW books the NORMAL exit unchanged
    assert len(sc._probe_calls) == 1
    assert len(sc._erd_recs) == 1
    rec = sc._erd_recs[0]
    assert rec["route_dead"] is True
    assert rec["token"] == "TOK"
    assert rec["address"] == "mintTOK"
    assert rec["exit_reason"] == "hard_stop"
    assert rec["booked_exit_price"] == pytest.approx(0.8)
    # booked pnl_pct = (0.8/1.0 - 1)*100 = -20%
    assert rec["booked_exit_pnl_pct"] == pytest.approx(-20.0)
    assert pm.closed == []                  # gate didn't preempt the close


# ---- 3) shadow + route ALIVE -> no dead line, normal exit -------------------

def test_shadow_alive_no_log(monkeypatch):
    monkeypatch.setenv("EXIT_ROUTE_DEAD_MODE", "shadow")
    sc, pos, pm = _scanner()
    _set_route(sc, alive=True)
    preempt = _run(sc._maybe_exit_route_dead(
        "badday_flush", pm, "TOK", pos,
        NS(reason="hard_stop"), 1.0, eff_exit=0.8, current_price=0.8, now=1.0))
    assert preempt is False
    assert len(sc._probe_calls) == 1        # probed (mode armed)
    assert sc._erd_recs == []               # route alive -> no "dead" line
    assert pm.closed == []


# ---- 4) PARTIAL exit -> gate does NOT fire (no probe, no log) ---------------

def test_partial_does_not_fire(monkeypatch):
    monkeypatch.setenv("EXIT_ROUTE_DEAD_MODE", "shadow")
    sc, pos, pm = _scanner()
    _set_route(sc, alive=False)             # dead, but partial must not probe
    preempt = _run(sc._maybe_exit_route_dead(
        "badday_flush", pm, "TOK", pos,
        NS(reason="TP1"), 0.75, eff_exit=1.2, current_price=1.2, now=1.0))
    assert preempt is False
    assert sc._probe_calls == []            # only full closes (>=1.0) probe
    assert sc._erd_recs == []


# ---- 5) enforce + route DEAD -> booked via no-route path -------------------

def test_enforce_dead_books_no_route(monkeypatch):
    monkeypatch.setenv("EXIT_ROUTE_DEAD_MODE", "enforce")
    sc, pos, pm = _scanner(entry_price=1.0)
    _set_route(sc, alive=False)
    preempt = _run(sc._maybe_exit_route_dead(
        "badday_flush", pm, "TOK", pos,
        NS(reason="hard_stop"), 1.0, eff_exit=0.8, current_price=0.8, now=7.0))
    assert preempt is True                   # caller MUST skip the clean-credit close
    assert len(sc._probe_calls) == 1
    # booked via the no-route path, NOT the clean exit-price credit
    assert len(pm.closed) == 1
    assert pm.closed[0].reason == "corpse_no_route"
    assert len(sc._record_calls) == 1
    rec, rec_bot = sc._record_calls[0]
    assert rec_bot == "badday_flush"
    assert rec["no_route"] is True
    assert rec["paper_close_no_fill"] is True


# ---- 5b) enforce + route ALIVE -> normal exit (no preempt) -----------------

def test_enforce_alive_normal_exit(monkeypatch):
    monkeypatch.setenv("EXIT_ROUTE_DEAD_MODE", "enforce")
    sc, pos, pm = _scanner()
    _set_route(sc, alive=True)
    preempt = _run(sc._maybe_exit_route_dead(
        "badday_flush", pm, "TOK", pos,
        NS(reason="hard_stop"), 1.0, eff_exit=0.8, current_price=0.8, now=1.0))
    assert preempt is False
    assert pm.closed == []                   # no no-route close; caller books normal


# ---- 6) fail-open: probe raises -> treated as alive, no crash ---------------

def test_failopen_probe_raises(monkeypatch):
    monkeypatch.setenv("EXIT_ROUTE_DEAD_MODE", "shadow")
    sc, pos, pm = _scanner()
    _set_route(sc, raises=True)
    preempt = _run(sc._maybe_exit_route_dead(
        "badday_flush", pm, "TOK", pos,
        NS(reason="hard_stop"), 1.0, eff_exit=0.8, current_price=0.8, now=1.0))
    assert preempt is False                  # uncertainty -> route ALIVE, normal exit
    assert sc._erd_recs == []                # no "dead" record on uncertainty
    assert pm.closed == []


def test_failopen_enforce_probe_raises(monkeypatch):
    monkeypatch.setenv("EXIT_ROUTE_DEAD_MODE", "enforce")
    sc, pos, pm = _scanner()
    _set_route(sc, raises=True)
    preempt = _run(sc._maybe_exit_route_dead(
        "badday_flush", pm, "TOK", pos,
        NS(reason="hard_stop"), 1.0, eff_exit=0.8, current_price=0.8, now=1.0))
    assert preempt is False                  # never enforce a no-route on uncertainty
    assert pm.closed == []


# ---- 7) shadow is NON-BLOCKING: probe runs OFF the hot path -----------------

def test_shadow_non_blocking_probe_offloop(monkeypatch):
    """A SLOW probe (blocks on an Event) must NOT delay the gate's return: shadow
    returns False PROMPTLY (before the probe completes), then the background task
    writes the JSONL record once the probe is driven to completion. Proves the
    await is off the serial decision loop's hot path."""
    monkeypatch.setenv("EXIT_ROUTE_DEAD_MODE", "shadow")
    sc, pos, pm = _scanner(entry_price=1.0)

    async def _drive():
        ev = asyncio.Event()

        async def _slow_probe(position):
            sc._probe_calls.append(position)
            await ev.wait()       # block until the test releases it
            return False          # route DEAD

        sc._corpse_has_route = _slow_probe

        preempt = await sc._maybe_exit_route_dead(
            "badday_flush", pm, "TOK", pos,
            NS(reason="hard_stop"), 1.0, eff_exit=0.8, current_price=0.8, now=9.0)
        assert preempt is False               # returned WITHOUT awaiting the probe
        assert sc._erd_recs == []             # nothing logged yet (probe pending)
        assert pm.closed == []                # gate didn't book anything itself

        # Let the background task start and block on the Event.
        await asyncio.sleep(0)
        assert sc._erd_recs == []             # still blocked inside the probe

        # Release the probe and drive the loop until the bg task writes.
        ev.set()
        for _ in range(20):
            await asyncio.sleep(0)
            if sc._erd_recs:
                break
        assert len(sc._probe_calls) == 1
        assert len(sc._erd_recs) == 1         # background task DID write the record
        rec = sc._erd_recs[0]
        assert rec["route_dead"] is True
        assert rec["mode"] == "shadow"
        assert rec["booked_exit_price"] == pytest.approx(0.8)
        assert rec["booked_exit_pnl_pct"] == pytest.approx(-20.0)

    _run(_drive())


# ---- 8) shadow background task SWALLOWS a raising probe ----------------------

def test_shadow_bg_swallows_raising_probe(monkeypatch):
    """A raising probe in the OFF-LOOP shadow task must not crash / surface an
    unobserved-exception, and must write NO 'dead' record (fail-open -> alive)."""
    monkeypatch.setenv("EXIT_ROUTE_DEAD_MODE", "shadow")
    sc, pos, pm = _scanner()
    _set_route(sc, raises=True)

    async def _drive():
        preempt = await sc._maybe_exit_route_dead(
            "badday_flush", pm, "TOK", pos,
            NS(reason="hard_stop"), 1.0, eff_exit=0.8, current_price=0.8, now=1.0)
        assert preempt is False
        # Drive the loop so the background task runs to completion.
        for _ in range(20):
            await asyncio.sleep(0)
        assert sc._probe_calls == [pos]       # probe was invoked off-loop
        assert sc._erd_recs == []             # raise -> treated alive -> no record
        assert pm.closed == []                # no crash, no booking
        # No tasks left pending (the bg task finished cleanly, exception retrieved).
        leftover = [t for t in getattr(sc, "_erd_bg_tasks", set()) if not t.done()]
        assert leftover == []

    _run(_drive())
