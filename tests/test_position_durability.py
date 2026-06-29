"""Position-durability tests (deploy-amnesia fix, 2026-06-29).

Fleet bots persist open positions to /data/bot_state/{bot_id}.json and restore
on boot. A position is ORPHANED (lost) when a redeploy coincides with a fresh
buy: the durable trades ledger has the BUY (append-mode, inline) but the
offloaded bot_state write never lands, so the bot_state restore finds 0.

Three fixes, each behind an env gate defaulting ON:
  1. POSITION_LEDGER_RECONCILE_MODE — boot reconcile MERGE that re-hydrates any
     buy-without-sell from the trades ledger that bot_state didn't restore.
  2. GRACEFUL_SHUTDOWN_FLUSH — SIGTERM/SIGINT/atexit synchronous flush.
  3. save_bot_state no-clobber — a capital-only save can't erase a populated
     open_positions book.
"""
import json

import pytest

from core.bot_config import BotConfig
from core.per_bot_capital import PerBotCapital
from core.per_bot_position_manager import PerBotPositionManager
from core.multi_bot_persistence import MultiBotTradeStore


def _cfg(bot_id="badday_flush", **overrides):
    base = dict(bot_id=bot_id, display_name=bot_id, max_concurrent_positions=3)
    base.update(overrides)
    return BotConfig(**base)


def _shell_scanner(store, bots):
    """A DipScanner shell (no __init__) carrying only what the reconcile +
    flush paths touch: trade_store, bot_position_managers, bot_capitals."""
    from feeds.dip_scanner import DipScanner
    sc = DipScanner.__new__(DipScanner)
    sc.trade_store = store
    sc.bot_position_managers = {}
    sc.bot_capitals = {}
    for bid in bots:
        cfg = _cfg(bot_id=bid)
        sc.bot_position_managers[bid] = PerBotPositionManager(cfg)
        sc.bot_capitals[bid] = PerBotCapital(bid, 2000.0)
    return sc


# --- Part 1: ledger-reconcile MERGE fallback -------------------------------

def test_ledger_reconcile_rehydrates_orphan(tmp_path):
    """bot_state restore yielded 0 for the bot, but the ledger has a
    buy-without-sell for token 'piss' -> after reconcile the pm holds it with
    remaining_fraction=1.0. Reproduces the 'piss' orphan."""
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.record_trade({
        "type": "buy", "token": "piss", "entry_price": 0.0005,
        "amount_usd": 25.0, "address": "PissMint111", "pair_address": "PissPair",
        "time": "2026-06-29T20:13:00+00:00",
    }, bot_id="badday_flush")

    sc = _shell_scanner(store, ["badday_flush"])
    pm = sc.bot_position_managers["badday_flush"]
    assert pm.open_count == 0  # bot_state restore found nothing

    sc._restore_open_positions_from_trades()

    assert "piss" in pm._positions
    p = pm._positions["piss"]
    assert p.entry_price == pytest.approx(0.0005)
    assert p.size_usd == pytest.approx(25.0)
    assert p.remaining_fraction == pytest.approx(1.0)
    assert p.tp1_hit is False
    assert p.peak_pnl_pct == pytest.approx(0.0)


def test_reconcile_merge_does_not_overwrite_botstate(tmp_path):
    """bot_state already restored token X with tp1_hit/peak/remaining; the ledger
    also has a buy for X -> reconcile must NOT downgrade or duplicate it."""
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.record_trade({
        "type": "buy", "token": "X", "entry_price": 0.01,
        "amount_usd": 20.0, "time": "2026-06-29T20:13:00+00:00",
    }, bot_id="badday_flush")

    sc = _shell_scanner(store, ["badday_flush"])
    pm = sc.bot_position_managers["badday_flush"]
    # Simulate the lossless bot_state restore of X (post-TP1 state).
    n = pm.load_state_list([{
        "token": "X", "entry_price": 0.008, "size_usd": 20.0,
        "entry_time": 111.0, "tp1_hit": True, "peak_pnl_pct": 50.0,
        "remaining_fraction": 0.5,
    }])
    assert n == 1

    sc._restore_open_positions_from_trades()

    assert pm.open_count == 1  # not duplicated
    p = pm._positions["X"]
    assert p.tp1_hit is True            # not downgraded
    assert p.peak_pnl_pct == pytest.approx(50.0)
    assert p.remaining_fraction == pytest.approx(0.5)
    assert p.entry_price == pytest.approx(0.008)  # bot_state entry, not ledger


def test_reconcile_failopen(tmp_path, monkeypatch):
    """A malformed/raising ledger must not break boot; existing positions stay."""
    store = MultiBotTradeStore(data_dir=tmp_path)
    sc = _shell_scanner(store, ["badday_flush"])
    pm = sc.bot_position_managers["badday_flush"]
    pm.open_position("KEEP", 0.01, 20.0, entry_time=1.0)

    def _boom(*a, **k):
        raise RuntimeError("corrupt ledger")

    monkeypatch.setattr(store, "load_trades", _boom)

    # Must not raise.
    sc._restore_open_positions_from_trades()

    assert "KEEP" in pm._positions
    assert pm.open_count == 1


def test_reconcile_off_gate_noop(tmp_path, monkeypatch):
    """POSITION_LEDGER_RECONCILE_MODE=off -> reconcile is a no-op."""
    monkeypatch.setenv("POSITION_LEDGER_RECONCILE_MODE", "off")
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.record_trade({
        "type": "buy", "token": "piss", "entry_price": 0.0005,
        "amount_usd": 25.0, "time": "2026-06-29T20:13:00+00:00",
    }, bot_id="badday_flush")
    sc = _shell_scanner(store, ["badday_flush"])
    pm = sc.bot_position_managers["badday_flush"]

    sc._restore_open_positions_from_trades()

    assert pm.open_count == 0  # gate off => not rehydrated


# --- Part 3: save_bot_state no-clobber -------------------------------------

def test_save_bot_state_preserves_open_positions_on_capital_only_save(tmp_path):
    """A capital-only save (dict lacks open_positions) must NOT erase a populated
    on-disk open_positions book."""
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.save_bot_state("b1", {
        "balance_usd": 1980.0, "in_flight_usd": 20.0,
        "open_positions": [{"token": "X", "entry_price": 0.01, "size_usd": 20.0}],
    })
    # Capital-only save (no open_positions key).
    store.save_bot_state("b1", {"balance_usd": 1975.0, "in_flight_usd": 25.0})

    loaded = store.load_bot_state("b1")
    assert loaded["balance_usd"] == 1975.0           # capital updated
    assert loaded.get("open_positions")              # positions preserved
    assert loaded["open_positions"][0]["token"] == "X"


def test_save_bot_state_explicit_empty_open_positions_clears(tmp_path):
    """An EXPLICIT empty open_positions list is honored (real close-to-flat)."""
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.save_bot_state("b1", {
        "balance_usd": 1980.0,
        "open_positions": [{"token": "X", "entry_price": 0.01, "size_usd": 20.0}],
    })
    store.save_bot_state("b1", {"balance_usd": 2000.0, "open_positions": []})
    loaded = store.load_bot_state("b1")
    assert loaded["open_positions"] == []


# --- Part 2: graceful-shutdown flush ---------------------------------------

def test_graceful_flush_persists_open_positions(tmp_path):
    """The flush fn, given a scanner holding a pm with a position, writes the
    bot_state to disk WITH that position (full path, not capital-only)."""
    import main as main_mod

    store = MultiBotTradeStore(data_dir=tmp_path)
    sc = _shell_scanner(store, ["badday_flush"])
    pm = sc.bot_position_managers["badday_flush"]
    pm.open_position("FLUSHME", 0.02, 30.0, entry_time=5.0)

    res = main_mod._flush_fleet_state(sc)

    assert res["bots_flushed"] == 1
    loaded = store.load_bot_state("badday_flush")
    assert loaded is not None
    toks = [p["token"] for p in loaded.get("open_positions", [])]
    assert "FLUSHME" in toks
