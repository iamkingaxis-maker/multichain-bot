# -*- coding: utf-8 -*-
"""PARTIAL-TP BURN guard (SHADOW-first, 2026-06-28).

The tier flag (tp1_hit/tp2_hit) is set in PerBotPositionManager.tick() BEFORE
the sell executes. On the LIVE sell path a transient failure returns None and
close_position is NEVER called -> 0 tokens sold but the flag stays set: the
partial is BURNED and every pre-TP1 loss-cutter (IN_FLIGHT_FLOOR / NEVER_RUNNER
/ NG_FASTSTOP / GIVEBACK_FLOOR, all gated on `not tp1_hit`) is silently
disabled. Paper never returns None so this is live-exclusive.

PARTIAL_BURN_MODE=off|shadow|enforce (default off). Default off = byte-identical:
the flag stays set (current behavior), no shadow record, no rollback."""
import asyncio

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
    monkeypatch.setenv("PARTIAL_BURN_MODE", val)
    assert rt_mode("PARTIAL_BURN_MODE") == expected


def test_flag_resolver_default_off(monkeypatch):
    monkeypatch.delenv("PARTIAL_BURN_MODE", raising=False)
    assert rt_mode("PARTIAL_BURN_MODE") == "off"


# ---- scanner harness -----------------------------------------------------

def _scanner_with_live_sell(monkeypatch, live_result, bot_id="badday_flush",
                            kind="TP1", sell_fraction=0.75,
                            tp1_hit=True, tp2_hit=False):
    """A DipScanner skeleton wired so _execute_bot_sell takes the LIVE path and
    _execute_bot_sell_live returns `live_result` (None = the failure return).

    The tier flag (tp1_hit/tp2_hit) is pre-set to mirror tick() having already
    stamped it before the sell. Returns (sc, pos, exit_decision)."""
    # Force the live route: should_route_live(live_probe, USE_JUPITER_ULTRA, has_key)
    monkeypatch.setattr("core.trader.USE_JUPITER_ULTRA", True, raising=False)

    sc = DipScanner.__new__(DipScanner)
    pos = NS(token="TOK", address="mintTOK", pair_address="pairTOK",
             entry_price=1.0, size_usd=20.0, entry_time=0.0,
             tp1_hit=tp1_hit, tp2_hit=tp2_hit,
             remaining_fraction=1.0, state_blob={})

    class _PM:
        def __init__(self):
            self.config = NS(bot_id=bot_id, live_probe=True)

        def get_position(self, token):
            return pos

        def close_position(self, token, exit_price, exit_time, reason, sell_fraction):
            # Only reached on a SUCCESSFUL live sell. cost_usd<=0 -> _execute_bot_sell
            # returns early (no trade_store / capital plumbing needed for the test).
            return NS(cost_usd=0.0, proceeds_usd=0.0, entry_price=1.0,
                      realized_pnl_usd=0.0, pnl_pct=0.0, peak_pnl_pct=0.0,
                      hold_secs=0.0, sell_fraction=sell_fraction, fully_closed=False)

    pm = _PM()
    sc.bot_position_managers = {bot_id: pm}
    sc.bot_capitals = {bot_id: NS(realize_sell=lambda cost_usd, proceeds_usd: None)}
    sc.trader = NS(private_key="deadbeef")
    sc._addr_by_token = {}

    async def _sell_live(token, pm_, pos_, sold_frac, current_mid):
        return live_result
    sc._execute_bot_sell_live = _sell_live

    # Capture shadow records in memory (the real writer is exercised separately).
    sc._burn_recs = []
    sc._append_partial_burn_shadow = lambda rec: sc._burn_recs.append(rec)

    decision = NS(token="TOK", kind=kind, reason=f"{kind} pnl=5.00% >= 5",
                  sell_fraction=sell_fraction)
    return sc, pos, decision


# ---- default OFF = byte-identical no-op ------------------------------------

def test_off_none_leaves_flag_set_no_record(monkeypatch):
    monkeypatch.delenv("PARTIAL_BURN_MODE", raising=False)
    sc, pos, decision = _scanner_with_live_sell(monkeypatch, live_result=None)
    _run(sc._execute_bot_sell("badday_flush", "TOK", decision, 1.05, 1000.0))
    # current behavior: flag stays set (the partial is burned), nothing recorded
    assert pos.tp1_hit is True
    assert sc._burn_recs == []
    assert pos.state_blob.get("partial_burn_count") is None


# ---- shadow: same behavior + a record ------------------------------------

def test_shadow_none_records_but_keeps_flag(monkeypatch, caplog):
    monkeypatch.setenv("PARTIAL_BURN_MODE", "shadow")
    sc, pos, decision = _scanner_with_live_sell(monkeypatch, live_result=None)
    import logging
    caplog.set_level(logging.INFO)
    _run(sc._execute_bot_sell("badday_flush", "TOK", decision, 1.05, 1000.0))
    # behavior UNCHANGED: flag stays set
    assert pos.tp1_hit is True
    # but a shadow record + counter + log were emitted
    assert len(sc._burn_recs) == 1
    rec = sc._burn_recs[0]
    assert rec["kind"] == "TP1"
    assert rec["mode"] == "shadow"
    assert rec["remaining_fraction"] == 1.0
    assert rec["pnl_pct"] == pytest.approx(5.0, abs=0.01)
    assert pos.state_blob.get("partial_burn_count") == 1
    assert pos.state_blob.get("partial_burn_kind") == "TP1"
    assert any("[partial-burn] SHADOW" in r.getMessage() for r in caplog.records)


# ---- enforce: rolls back the flag so the partial re-issues -----------------

def test_enforce_none_rolls_back_tp1(monkeypatch):
    monkeypatch.setenv("PARTIAL_BURN_MODE", "enforce")
    sc, pos, decision = _scanner_with_live_sell(monkeypatch, live_result=None)
    _run(sc._execute_bot_sell("badday_flush", "TOK", decision, 1.05, 1000.0))
    # rolled back -> partial RE-ISSUES next tick, pre-TP1 loss-cutters re-enabled
    assert pos.tp1_hit is False
    assert len(sc._burn_recs) == 1
    assert sc._burn_recs[0]["mode"] == "enforce"


def test_enforce_none_rolls_back_tp2_only(monkeypatch):
    monkeypatch.setenv("PARTIAL_BURN_MODE", "enforce")
    sc, pos, decision = _scanner_with_live_sell(
        monkeypatch, live_result=None, kind="TP2", sell_fraction=0.5,
        tp1_hit=True, tp2_hit=True)
    _run(sc._execute_bot_sell("badday_flush", "TOK", decision, 1.05, 1000.0))
    # only the TP2 flag rolls back; TP1 stays booked
    assert pos.tp2_hit is False
    assert pos.tp1_hit is True


# ---- successful sell: never a burn, flag stays set in ALL modes ------------

@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_successful_sell_keeps_flag_no_record(monkeypatch, mode):
    monkeypatch.setenv("PARTIAL_BURN_MODE", mode)
    sc, pos, decision = _scanner_with_live_sell(
        monkeypatch,
        live_result={"exit_price": 1.05, "instrument": {"live_side": "sell"}})
    _run(sc._execute_bot_sell("badday_flush", "TOK", decision, 1.05, 1000.0))
    # a real fill -> _lr is not None -> burn guard never runs
    assert pos.tp1_hit is True
    assert sc._burn_recs == []
    assert pos.state_blob.get("partial_burn_count") is None


# ---- non-tier exit kinds are ignored even on a None live sell -------------

def test_enforce_ignores_full_exit_kind(monkeypatch):
    monkeypatch.setenv("PARTIAL_BURN_MODE", "enforce")
    sc, pos, decision = _scanner_with_live_sell(
        monkeypatch, live_result=None, kind="HARD_STOP", sell_fraction=1.0)
    _run(sc._execute_bot_sell("badday_flush", "TOK", decision, 0.9, 1000.0))
    # HARD_STOP doesn't set a tp flag -> no rollback, no record
    assert sc._burn_recs == []
    assert pos.tp1_hit is True


# ---- the real JSONL writer works (fail-open, tmp DATA_DIR) -----------------

def test_real_writer_appends_jsonl(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    sc = DipScanner.__new__(DipScanner)
    sc._append_partial_burn_shadow({"kind": "TP1", "bot_id": "b"})
    out = tmp_path / "partial_burn_shadow.jsonl"
    assert out.exists()
    import json
    rec = json.loads(out.read_text().strip())
    assert rec["kind"] == "TP1"
