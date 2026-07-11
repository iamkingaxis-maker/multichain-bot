"""Append-mode reader freshness (2026-06-22).

In LEDGER_APPEND_MODE, the base trades_multi.json is frozen once the mode is
on; all new fills go to the trades_multi.jsonl sidecar. A READER instance
(e.g. the dashboard's MultiBotTradeStore, separate object from the bot's)
loaded its in-memory ledger once at boot and never replayed the sidecar — so
/api/trades went stale (stuck at the last full base write) and never showed
trades the bot appended after boot. load_trades must reflect base + sidecar
so any instance is current, without double-counting on the writer instance.
"""
import json
import os

import pytest

from core.multi_bot_persistence import MultiBotTradeStore


@pytest.fixture
def append_mode(monkeypatch):
    monkeypatch.setenv("LEDGER_APPEND_MODE", "on")
    # These tests pin READER FRESHNESS with fixed 2026-06 trade times; once
    # those dates aged past LEDGER_ROTATE_DAYS the boot rotation (2026-07-11,
    # #496 memory cut — pinned in test_ledger_rotation.py) correctly archives
    # them out of the base. Rotation is off here so the fixture rows persist.
    monkeypatch.setenv("LEDGER_ROTATE_DAYS", "0")


def _trade(symbol, bot="badday_flush_nf15_live", pnl=1.0):
    return {"type": "sell", "token": symbol, "pnl_pct": pnl, "time": "2026-06-22T00:00:00Z"}


def test_reader_instance_sees_writer_appends(tmp_path, append_mode):
    """A separate reader instance (dashboard) must see trades the writer
    appended after the reader booted."""
    writer = MultiBotTradeStore(tmp_path)
    reader = MultiBotTradeStore(tmp_path)
    # reader loads its ledger (empty) at boot
    assert reader.load_trades() == []
    # writer records a trade AFTER the reader booted
    writer.record_trade(_trade("QAI"), "badday_flush_nf15_live")
    # reader must now see it (was the bug: stayed empty)
    syms = [t.get("token") for t in reader.load_trades()]
    assert "QAI" in syms, f"reader stale — did not see writer's append: {syms}"


def test_writer_no_double_count(tmp_path, append_mode):
    """The writer's own load_trades returns each record exactly once."""
    w = MultiBotTradeStore(tmp_path)
    w.record_trade(_trade("ANSEM"), "badday_flush_nf15_live")
    w.record_trade(_trade("Lasse"), "badday_flush_nf15_live")
    toks = [t.get("token") for t in w.load_trades()]
    assert toks.count("ANSEM") == 1, f"double-count: {toks}"
    assert toks.count("Lasse") == 1, f"double-count: {toks}"
    assert len(w.load_trades()) == 2


def test_base_plus_sidecar(tmp_path, append_mode):
    """Pre-existing base records + sidecar appends both appear (current)."""
    base = [{"type": "sell", "token": "OLD", "bot_id": "x", "time": "2026-06-19T00:00:00Z"}]
    (tmp_path / "trades_multi.json").write_text(json.dumps(base))
    s = MultiBotTradeStore(tmp_path)
    s.record_trade(_trade("NEW"), "badday_flush_nf15_live")
    toks = [t.get("token") for t in s.load_trades()]
    assert "OLD" in toks and "NEW" in toks, toks


def test_bot_id_filter(tmp_path, append_mode):
    s = MultiBotTradeStore(tmp_path)
    s.record_trade(_trade("A"), "badday_flush_nf15_live")
    s.record_trade(_trade("B"), "other_bot")
    live = [t.get("token") for t in s.load_trades(bot_id="badday_flush_nf15_live")]
    assert live == ["A"], live
