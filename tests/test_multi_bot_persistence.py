import asyncio
import json
import pytest
from pathlib import Path
from core.multi_bot_persistence import MultiBotTradeStore


def test_record_trade_stamps_bot_id(tmp_path):
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.record_trade({
        "type": "buy", "token": "SQUIRE", "entry_price": 0.001,
        "amount_usd": 20.0, "time": "2026-05-23T10:00:00+00:00",
    }, bot_id="baseline_v1")
    # Option B split: multi-bot file is trades_multi.json (was trades.json).
    trades_file = tmp_path / "trades_multi.json"
    assert trades_file.exists()
    data = json.loads(trades_file.read_text())
    assert len(data) == 1
    assert data[0]["bot_id"] == "baseline_v1"
    assert data[0]["token"] == "SQUIRE"


def test_load_trades_filters_by_bot_id(tmp_path):
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.record_trade({"type": "buy", "token": "A", "time": "t1"}, bot_id="b1")
    store.record_trade({"type": "buy", "token": "B", "time": "t2"}, bot_id="b2")
    store.record_trade({"type": "buy", "token": "C", "time": "t3"}, bot_id="b1")
    b1_trades = store.load_trades(bot_id="b1")
    assert len(b1_trades) == 2
    assert {t["token"] for t in b1_trades} == {"A", "C"}
    b2_trades = store.load_trades(bot_id="b2")
    assert len(b2_trades) == 1
    assert b2_trades[0]["token"] == "B"


def test_load_trades_no_filter_returns_all(tmp_path):
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.record_trade({"type": "buy", "token": "A", "time": "t1"}, bot_id="b1")
    store.record_trade({"type": "buy", "token": "B", "time": "t2"}, bot_id="b2")
    assert len(store.load_trades()) == 2


def test_split_migration_partitions_legacy_trades(tmp_path):
    """Option B split: pre-split trades.json gets partitioned on first boot.
    Legacy/baseline_v1 records stay in trades.json; multi-bot records move
    to trades_multi.json. load_trades() (which reads trades_multi.json)
    returns only the multi-bot share."""
    mixed = [
        {"type": "buy", "token": "OLD", "time": "t0"},  # no bot_id => legacy
        {"type": "buy", "token": "BASE", "time": "t1", "bot_id": "baseline_v1"},
        {"type": "buy", "token": "MULTI_A", "time": "t2", "bot_id": "no_filters"},
        {"type": "buy", "token": "MULTI_B", "time": "t3", "bot_id": "tod_morning"},
    ]
    (tmp_path / "trades.json").write_text(json.dumps(mixed))
    store = MultiBotTradeStore(data_dir=tmp_path)
    # Migration ran in constructor
    sentinel = tmp_path / ".trades_split_v1"
    assert sentinel.exists()
    # Multi-bot file now exists with only the multi-bot records
    multi = json.loads((tmp_path / "trades_multi.json").read_text())
    assert len(multi) == 2
    assert {t["token"] for t in multi} == {"MULTI_A", "MULTI_B"}
    # Legacy file retains baseline + no-bot_id records
    legacy = json.loads((tmp_path / "trades.json").read_text())
    assert len(legacy) == 2
    assert {t["token"] for t in legacy} == {"OLD", "BASE"}
    # load_trades returns only the multi-bot share
    loaded = store.load_trades()
    assert {t["token"] for t in loaded} == {"MULTI_A", "MULTI_B"}


def test_split_migration_idempotent(tmp_path):
    """Re-running the migration is a no-op once the sentinel exists."""
    mixed = [{"type": "buy", "token": "M", "time": "t0", "bot_id": "no_filters"}]
    (tmp_path / "trades.json").write_text(json.dumps(mixed))
    MultiBotTradeStore(data_dir=tmp_path)  # first run: splits
    # Mutate trades_multi.json to verify second run doesn't re-split
    (tmp_path / "trades_multi.json").write_text(json.dumps([{"bot_id": "x", "token": "MUTATED"}]))
    MultiBotTradeStore(data_dir=tmp_path)  # second run: no-op
    multi = json.loads((tmp_path / "trades_multi.json").read_text())
    assert multi[0]["token"] == "MUTATED"


def test_bot_state_save_load_roundtrip(tmp_path):
    from core.per_bot_capital import PerBotCapital
    store = MultiBotTradeStore(data_dir=tmp_path)
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=2000.0)
    cap.reserve_for_buy(20.0)
    store.save_bot_state("b1", cap.to_dict())
    loaded = store.load_bot_state("b1")
    assert loaded["balance_usd"] == 1980.0
    assert loaded["in_flight_usd"] == 20.0


def test_load_bot_state_returns_None_when_missing(tmp_path):
    store = MultiBotTradeStore(data_dir=tmp_path)
    assert store.load_bot_state("nonexistent") is None


# ---------------------------------------------------------------------------
# Loop-freeze fix (2026-06-19): per-fill ledger writes are offloaded off the
# event loop via asyncio.to_thread so a slow O(history) write can't freeze the
# trading loop. The offload must still persist durably, serialize via the
# threading.Lock, and fall back to pure-sync when there is no running loop.
# Gated behind LEDGER_WRITE_OFFLOAD (default on).
# ---------------------------------------------------------------------------

def test_record_trade_async_roundtrip_when_offloaded(tmp_path, monkeypatch):
    """(a) record_trade_async persists correctly (read back) when offloaded."""
    monkeypatch.setenv("LEDGER_WRITE_OFFLOAD", "on")
    store = MultiBotTradeStore(data_dir=tmp_path)

    async def _go():
        await store.record_trade_async(
            {"type": "buy", "token": "OFF", "time": "t1"}, bot_id="b1")

    asyncio.run(_go())
    data = json.loads((tmp_path / "trades_multi.json").read_text())
    assert len(data) == 1
    assert data[0]["token"] == "OFF"
    assert data[0]["bot_id"] == "b1"


def test_concurrent_offloaded_record_trade_no_loss(tmp_path, monkeypatch):
    """(b) Many concurrent offloaded record_trade calls don't corrupt the file
    or lose records — the threading.Lock serializes them inside the worker."""
    monkeypatch.setenv("LEDGER_WRITE_OFFLOAD", "on")
    store = MultiBotTradeStore(data_dir=tmp_path)
    N = 40

    async def _go():
        await asyncio.gather(*[
            store.record_trade_async(
                {"type": "buy", "token": f"T{i}", "time": f"t{i}"}, bot_id="b1")
            for i in range(N)
        ])

    asyncio.run(_go())
    data = json.loads((tmp_path / "trades_multi.json").read_text())
    assert len(data) == N
    assert {t["token"] for t in data} == {f"T{i}" for i in range(N)}


def test_record_trade_async_falls_back_to_sync_without_loop(tmp_path, monkeypatch):
    """(c) The offload helper runs the write synchronously when called with no
    running event loop (important for non-async callers and tests)."""
    monkeypatch.setenv("LEDGER_WRITE_OFFLOAD", "on")
    store = MultiBotTradeStore(data_dir=tmp_path)
    # _offload_write is a coroutine-free helper: call the sync entry directly.
    store._offload_write_sync(
        store.record_trade, {"type": "buy", "token": "SYNC", "time": "t1"},
        bot_id="b1")
    data = json.loads((tmp_path / "trades_multi.json").read_text())
    assert len(data) == 1
    assert data[0]["token"] == "SYNC"


def test_offload_disabled_uses_pure_sync(tmp_path, monkeypatch):
    """(d) LEDGER_WRITE_OFFLOAD=off => record_trade_async never touches a
    thread; it writes synchronously on the calling thread."""
    monkeypatch.setenv("LEDGER_WRITE_OFFLOAD", "off")
    store = MultiBotTradeStore(data_dir=tmp_path)

    called = {"to_thread": 0}
    real_to_thread = asyncio.to_thread

    async def _spy(fn, *a, **k):
        called["to_thread"] += 1
        return await real_to_thread(fn, *a, **k)

    monkeypatch.setattr(asyncio, "to_thread", _spy)

    async def _go():
        await store.record_trade_async(
            {"type": "buy", "token": "PURESYNC", "time": "t1"}, bot_id="b1")

    asyncio.run(_go())
    data = json.loads((tmp_path / "trades_multi.json").read_text())
    assert len(data) == 1
    assert data[0]["token"] == "PURESYNC"
    assert called["to_thread"] == 0


def test_save_bot_state_async_roundtrip(tmp_path):
    """save_bot_state_async persists durably when offloaded."""
    from core.per_bot_capital import PerBotCapital
    store = MultiBotTradeStore(data_dir=tmp_path)
    cap = PerBotCapital(bot_id="b1", starting_balance_usd=2000.0)
    cap.reserve_for_buy(20.0)

    async def _go():
        await store.save_bot_state_async("b1", cap.to_dict())

    asyncio.run(_go())
    loaded = store.load_bot_state("b1")
    assert loaded["balance_usd"] == 1980.0
    assert loaded["in_flight_usd"] == 20.0
