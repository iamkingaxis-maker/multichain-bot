# -*- coding: utf-8 -*-
"""EXIT-ARM fresh-confirm gate (SHADOW-first, EXIT_ARM_MODE, default off).

Exit-side mirror of the BUY arm_only stale-price fix. The slow ~150s
DexScreener tick decides an exit trigger fires on the STALE snapshot price;
EXIT_ARM re-evaluates that armed exit on the FRESH Jupiter price before it
commits:

  PROFIT triggers (TP1/TP2/trail/giveback/breakeven-lock) fire ONLY if the fresh
  price STILL reproduces them — a stale DexScreener SPIKE Jupiter never reached
  is a PHANTOM TP and is REJECTED (held). LOSS triggers (stop/floor/never_runner)
  confirm on fresh and cut; a rejection means fresh is BETTER than stale (hold).

EXIT_ARM_MODE=off|shadow|enforce (default off). off = byte-identical: the gate
is never even invoked (the caller short-circuits on _arm_on). shadow = re-eval
OFF-LOOP + JSONL, but BOOK the stale-tick exit exactly as today (zero P&L
change). enforce = gate the exit on fresh confirmation; a rejected profit exit
HOLDS with its tier/peak flags rolled back so the loss floor stays live.
"""
import asyncio
import os

import pytest

from types import SimpleNamespace as NS

from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager, OpenPosition
from feeds.dip_scanner import DipScanner


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _cfg(bot_id="badday_flush"):
    # tp1 at +5%, hard stop at -15%, in-flight floor scope is badday_*; keep the
    # ladder simple so a single trigger fires per tick.
    return BotConfig(
        bot_id=bot_id, display_name=bot_id,
        tp1_pct=5.0, tp1_sell_fraction=0.75,
        tp2_pct=12.0, tp2_sell_fraction=0.25,
        trail_pp=3.0, hard_stop_pct=-15.0,
    )


def _scanner(bot_id="badday_flush", entry_price=1.0, addr="mintTOK"):
    """A DipScanner skeleton + a real PerBotPositionManager holding one position."""
    sc = DipScanner.__new__(DipScanner)
    cfg = _cfg(bot_id)
    pm = PerBotPositionManager(cfg)
    pos = OpenPosition(
        token="TOK", entry_price=entry_price, size_usd=20.0, entry_time=0.0,
        address=addr, pair_address="pairTOK", state_blob={})
    pm._positions["TOK"] = pos
    sc.bot_position_managers = {bot_id: pm}
    sc._addr_by_token = {"TOK": addr}
    sc._fast_samples = {}
    # capture JSONL writes instead of touching disk
    sc._arm_recs = []
    sc._append_exit_arm_shadow = lambda rec: sc._arm_recs.append(rec)
    # default fresh-price stub; tests override per-case
    sc._fresh_calls = []

    async def _fresh(token, address="", pair_address=""):
        sc._fresh_calls.append((token, address))
        return None
    sc._get_current_price_for = _fresh
    return sc, pm, pos


def _set_fresh(sc, price):
    async def _fresh(token, address="", pair_address=""):
        sc._fresh_calls.append((token, address))
        return price
    sc._get_current_price_for = _fresh


# --------------------------------------------------------------------------- #
# 1) off  -> byte-identical: the gate is never reached, no fresh-fetch/log.    #
# --------------------------------------------------------------------------- #

def test_off_byte_identical_no_fresh_no_log(monkeypatch):
    """With EXIT_ARM_MODE off the gate, if it WERE called, must short-circuit
    BEFORE any fresh fetch / re-eval / log and return the decisions unchanged.
    (The production caller skips it entirely via _arm_on; this guards the gate's
    own off-path too.)"""
    monkeypatch.delenv("EXIT_ARM_MODE", raising=False)
    sc, pm, pos = _scanner(entry_price=1.0)

    # spy: fresh-price MUST NOT be called in off
    called = {"fresh": False}

    async def _spy(token, address="", pair_address=""):
        called["fresh"] = True
        return 2.0
    sc._get_current_price_for = _spy

    snap = OpenPosition(token="TOK", entry_price=1.0, size_usd=20.0,
                        entry_time=0.0, address="mintTOK", state_blob={})
    decisions = [NS(kind="TP1", reason="TP1", sell_fraction=0.75)]
    out = _run(sc._exit_arm_gate("badday_flush", pm, "TOK", pos, snap,
                                 decisions, 2.0, 10.0, None))
    assert out is decisions          # returned unchanged
    assert called["fresh"] is False  # no fresh fetch
    assert sc._arm_recs == []         # no log


# --------------------------------------------------------------------------- #
# 2) shadow + stale-spike TP not confirmed by fresh -> behavior UNCHANGED      #
#    (exit still books today's way) + a "reject" JSONL record is written.      #
# --------------------------------------------------------------------------- #

def test_shadow_phantom_tp_books_unchanged_logs_reject(monkeypatch):
    monkeypatch.setenv("EXIT_ARM_MODE", "shadow")
    sc, pm, pos = _scanner(entry_price=1.0)
    # stale spike to +6% fired TP1 on the real tick; fresh is only +2% (below tp1).
    _set_fresh(sc, 1.02)
    snap = OpenPosition(token="TOK", entry_price=1.0, size_usd=20.0,
                        entry_time=0.0, address="mintTOK", state_blob={})
    decisions = [NS(kind="TP1", reason="TP1 pnl=6.00%", sell_fraction=0.75)]

    async def _drive():
        out = await sc._exit_arm_gate("badday_flush", pm, "TOK", pos, snap,
                                      decisions, 1.06, 30.0, None)
        # SHADOW books the NORMAL exit unchanged: decisions returned as-is.
        assert out is decisions
        # drive the off-loop probe to completion
        for _ in range(30):
            await asyncio.sleep(0)
            if sc._arm_recs:
                break
        assert len(sc._arm_recs) == 1
        rec = sc._arm_recs[0]
        assert rec["kind"] == "TP1"
        assert rec["trigger_class"] == "profit"
        assert rec["decision"] == "reject"
        assert rec["would_fire"] is False
        assert rec["mode"] == "shadow"
        assert rec["stale_pnl_pct"] == pytest.approx(6.0)
        assert rec["fresh_pnl_pct"] == pytest.approx(2.0)
    _run(_drive())


def test_shadow_pnl_identical_to_off(monkeypatch):
    """The decisions list shadow returns is byte-identical to what off returns:
    the exit books the SAME way (zero P&L change) whether shadow rejects or not."""
    sc, pm, pos = _scanner(entry_price=1.0)
    snap = OpenPosition(token="TOK", entry_price=1.0, size_usd=20.0,
                        entry_time=0.0, address="mintTOK", state_blob={})
    decisions = [NS(kind="TP1", reason="TP1", sell_fraction=0.75)]

    monkeypatch.setenv("EXIT_ARM_MODE", "shadow")
    _set_fresh(sc, 1.02)  # fresh would REJECT
    out_shadow = _run(sc._exit_arm_gate("badday_flush", pm, "TOK", pos, snap,
                                        decisions, 1.06, 30.0, None))
    monkeypatch.setenv("EXIT_ARM_MODE", "off")
    out_off = _run(sc._exit_arm_gate("badday_flush", pm, "TOK", pos, snap,
                                     decisions, 1.06, 30.0, None))
    # both return the SAME decisions object -> identical booking
    assert out_shadow is decisions and out_off is decisions


# --------------------------------------------------------------------------- #
# 3) shadow probe is NON-BLOCKING: a slow/Event-gated fresh-fetch must not      #
#    delay the gate's return; the bg task writes the record afterwards.        #
# --------------------------------------------------------------------------- #

def test_shadow_non_blocking_probe_offloop(monkeypatch):
    monkeypatch.setenv("EXIT_ARM_MODE", "shadow")
    sc, pm, pos = _scanner(entry_price=1.0)
    snap = OpenPosition(token="TOK", entry_price=1.0, size_usd=20.0,
                        entry_time=0.0, address="mintTOK", state_blob={})
    decisions = [NS(kind="TP1", reason="TP1", sell_fraction=0.75)]

    async def _drive():
        ev = asyncio.Event()

        async def _slow_fresh(token, address="", pair_address=""):
            await ev.wait()   # block until released
            return 1.02       # fresh below tp1 -> would reject
        sc._get_current_price_for = _slow_fresh

        out = await sc._exit_arm_gate("badday_flush", pm, "TOK", pos, snap,
                                      decisions, 1.06, 9.0, None)
        assert out is decisions       # returned WITHOUT awaiting the probe
        assert sc._arm_recs == []     # nothing logged yet (probe pending)
        await asyncio.sleep(0)
        assert sc._arm_recs == []     # still blocked inside the fresh fetch
        ev.set()
        for _ in range(30):
            await asyncio.sleep(0)
            if sc._arm_recs:
                break
        assert len(sc._arm_recs) == 1          # bg task DID write
        assert sc._arm_recs[0]["decision"] == "reject"
        leftover = [t for t in getattr(sc, "_exit_arm_bg_tasks", set())
                    if not t.done()]
        assert leftover == []
    _run(_drive())


# --------------------------------------------------------------------------- #
# 4) enforce + stale-spike TP, fresh BELOW threshold -> exit does NOT fire     #
#    (held) AND the loss floor still fires on the held position.               #
# --------------------------------------------------------------------------- #

def test_enforce_phantom_tp_rejected_and_floor_stays_live(monkeypatch):
    monkeypatch.setenv("EXIT_ARM_MODE", "enforce")
    monkeypatch.setenv("IN_FLIGHT_FLOOR_MODE", "enforce")
    sc, pm, pos = _scanner(entry_price=1.0)
    # Real tick at a stale +6% spike sets tp1_hit + peak; we simulate that mutated
    # state on the real position, with the PRE-tick snapshot still pre-TP1.
    snap = OpenPosition(token="TOK", entry_price=1.0, size_usd=20.0,
                        entry_time=0.0, address="mintTOK", state_blob={},
                        tp1_hit=False, peak_pnl_pct=0.0)
    pos.tp1_hit = True            # the stale tick already set this
    pos.peak_pnl_pct = 6.0        # ...and bumped peak to the phantom spike
    decisions = [NS(kind="TP1", reason="TP1 pnl=6.00%", sell_fraction=0.75)]

    # fresh is a deep flush to -8% (below the -7 in-flight floor)
    _set_fresh(sc, 0.92)
    out = _run(sc._exit_arm_gate("badday_flush", pm, "TOK", pos, snap,
                                 decisions, 1.06, 30.0, None))
    assert out == []                       # phantom TP REJECTED -> no exit fires
    # tier/peak rolled back so loss-cutters are live again
    assert pos.tp1_hit is False
    assert pos.peak_pnl_pct == pytest.approx(0.0)
    # the held position is still PROTECTED: ticking it at the fresh-low price now
    # fires the in-flight loss floor (it was dark while tp1_hit was set).
    loss = pm.tick(token="TOK", current_price=0.92, now=31.0, vol_m5_usd=None)
    assert any(d.kind in ("IN_FLIGHT_FLOOR", "HARD_STOP") for d in loss)


# --------------------------------------------------------------------------- #
# 5) enforce + TP genuinely confirmed by fresh -> exit fires normally.         #
# --------------------------------------------------------------------------- #

def test_enforce_confirmed_tp_fires(monkeypatch):
    monkeypatch.setenv("EXIT_ARM_MODE", "enforce")
    sc, pm, pos = _scanner(entry_price=1.0)
    snap = OpenPosition(token="TOK", entry_price=1.0, size_usd=20.0,
                        entry_time=0.0, address="mintTOK", state_blob={})
    pos.tp1_hit = True
    decisions = [NS(kind="TP1", reason="TP1", sell_fraction=0.75)]
    _set_fresh(sc, 1.06)  # fresh ALSO above tp1 -> confirm
    out = _run(sc._exit_arm_gate("badday_flush", pm, "TOK", pos, snap,
                                 decisions, 1.06, 30.0, None))
    assert out == decisions          # confirmed -> fires normally
    assert pos.tp1_hit is True       # no rollback on a confirmed exit


# --------------------------------------------------------------------------- #
# 6) enforce + LOSS stop: fresh confirms -> cut; fresh BETTER -> hold.         #
# --------------------------------------------------------------------------- #

def test_enforce_loss_stop_confirmed_cuts(monkeypatch):
    monkeypatch.setenv("EXIT_ARM_MODE", "enforce")
    sc, pm, pos = _scanner(entry_price=1.0)
    snap = OpenPosition(token="TOK", entry_price=1.0, size_usd=20.0,
                        entry_time=0.0, address="mintTOK", state_blob={})
    decisions = [NS(kind="HARD_STOP", reason="hard stop", sell_fraction=1.0)]
    _set_fresh(sc, 0.80)  # fresh -20% also below the -15 hard stop -> confirm
    out = _run(sc._exit_arm_gate("badday_flush", pm, "TOK", pos, snap,
                                 decisions, 0.80, 30.0, None))
    assert out == decisions          # loss confirmed -> cut


def test_enforce_loss_stop_fresh_better_holds(monkeypatch):
    monkeypatch.setenv("EXIT_ARM_MODE", "enforce")
    sc, pm, pos = _scanner(entry_price=1.0)
    snap = OpenPosition(token="TOK", entry_price=1.0, size_usd=20.0,
                        entry_time=0.0, address="mintTOK", state_blob={})
    decisions = [NS(kind="HARD_STOP", reason="hard stop", sell_fraction=1.0)]
    # stale -16% fired the stop, but fresh recovered to -2% (above the stop)
    _set_fresh(sc, 0.98)
    out = _run(sc._exit_arm_gate("badday_flush", pm, "TOK", pos, snap,
                                 decisions, 0.84, 30.0, None))
    assert out == []                 # fresh BETTER than stale -> hold (correct)


# --------------------------------------------------------------------------- #
# 7) fail-open: fresh None / fetch raises -> falls back to current behavior.   #
# --------------------------------------------------------------------------- #

def test_failopen_enforce_fresh_none(monkeypatch):
    monkeypatch.setenv("EXIT_ARM_MODE", "enforce")
    sc, pm, pos = _scanner(entry_price=1.0)
    snap = OpenPosition(token="TOK", entry_price=1.0, size_usd=20.0,
                        entry_time=0.0, address="mintTOK", state_blob={})
    decisions = [NS(kind="TP1", reason="TP1", sell_fraction=0.75)]
    sc._fast_samples = {}             # no fast sample either
    # fresh fetch returns None (default stub) -> fail-open to today's behavior
    out = _run(sc._exit_arm_gate("badday_flush", pm, "TOK", pos, snap,
                                 decisions, 1.06, 30.0, None))
    assert out is decisions          # un-gated: fires on the stale tick


def test_failopen_enforce_fresh_raises(monkeypatch):
    monkeypatch.setenv("EXIT_ARM_MODE", "enforce")
    sc, pm, pos = _scanner(entry_price=1.0)
    snap = OpenPosition(token="TOK", entry_price=1.0, size_usd=20.0,
                        entry_time=0.0, address="mintTOK", state_blob={})
    decisions = [NS(kind="TP1", reason="TP1", sell_fraction=0.75)]

    async def _boom(token, address="", pair_address=""):
        raise RuntimeError("quote timeout")
    sc._get_current_price_for = _boom
    sc._fast_samples = {}
    out = _run(sc._exit_arm_gate("badday_flush", pm, "TOK", pos, snap,
                                 decisions, 1.06, 30.0, None))
    assert out is decisions          # fetch raised -> fail-open, no crash


def test_failopen_shadow_bg_swallows_raising_fresh(monkeypatch):
    """A raising fresh fetch in the OFF-LOOP shadow task must not crash / surface
    an unobserved-exception, and writes NO record."""
    monkeypatch.setenv("EXIT_ARM_MODE", "shadow")
    sc, pm, pos = _scanner(entry_price=1.0)
    snap = OpenPosition(token="TOK", entry_price=1.0, size_usd=20.0,
                        entry_time=0.0, address="mintTOK", state_blob={})
    decisions = [NS(kind="TP1", reason="TP1", sell_fraction=0.75)]

    async def _boom(token, address="", pair_address=""):
        raise RuntimeError("quote timeout")
    sc._get_current_price_for = _boom
    sc._fast_samples = {}

    async def _drive():
        out = await sc._exit_arm_gate("badday_flush", pm, "TOK", pos, snap,
                                      decisions, 1.06, 30.0, None)
        assert out is decisions
        for _ in range(30):
            await asyncio.sleep(0)
        assert sc._arm_recs == []     # no record on uncertainty
        leftover = [t for t in getattr(sc, "_exit_arm_bg_tasks", set())
                    if not t.done()]
        assert leftover == []
    _run(_drive())


# --------------------------------------------------------------------------- #
# 8) fresh-price fallback to the fast-sample buffer when the live fetch misses.#
# --------------------------------------------------------------------------- #

def test_fresh_price_falls_back_to_fast_sample(monkeypatch):
    from collections import deque
    sc, pm, pos = _scanner(entry_price=1.0)
    # live fetch returns None; a fresh fast-watch sample exists.
    sc._fast_samples = {"mintTOK": deque([1.02], maxlen=20)}
    px = _run(sc._exit_arm_fresh_price("TOK", pos))
    assert px == pytest.approx(1.02)
