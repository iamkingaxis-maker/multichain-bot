"""Ledger rotation + entry_meta cache trim + streamed boot compaction
(#496 Railway memory cuts, 2026-07-11).

Pins the hard invariants:
  1. LEADERBOARD IDENTITY — per-bot (total_pnl_realized, total_trades, wins)
     computed the dashboard's way (core/ledger_stats.sell_stats over active
     sells + the rotation stats fold) are IDENTICAL before/after rotation,
     and stable across repeated reboots.
  2. Boot daily-pnl re-derivation still sees every row of TODAY (the daily
     circuit breakers re-derive from the ledger at boot).
  3. entry_meta trim: newest LEDGER_META_KEEP_ROWS rows keep full meta; older
     cache rows are slimmed to the whitelist; DISK stays lossless.
  4. Streamed compaction write round-trips losslessly.
  5. Fail-open: any rotation error -> the FULL ledger loads (current behavior).
"""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from core.ledger_stats import sell_stats
from core.multi_bot_persistence import MultiBotTradeStore


def _iso(days_ago: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _buy(bot, tok, ep, days_ago, meta=None):
    return {"type": "buy", "bot_id": bot, "token": tok, "entry_price": ep,
            "amount_usd": 20.0, "time": _iso(days_ago),
            "entry_meta": meta if meta is not None else {"pc_h6": -3.0}}


def _sell(bot, tok, ep, pnl, days_ago, reason="tp", **kw):
    return {"type": "sell", "bot_id": bot, "token": tok, "entry_price": ep,
            "pnl": pnl, "pnl_pct": pnl, "time": _iso(days_ago),
            "reason": reason, **kw}


def _write_base(tmp_path, rows):
    (tmp_path / "trades_multi.json").write_text(json.dumps(rows))


def _lb_totals(store, bot_id):
    """Leaderboard math exactly as dashboard _build_bot_rows computes it:
    filter sells (cancelled-on-restart skip), then sell_stats + archive fold."""
    sells = [t for t in store.load_trades(bot_id=bot_id)
             if t.get("type") == "sell"
             and "cancelled on restart" not in (t.get("reason") or "")]
    arch = (store.load_rotation_stats().get("bots") or {}).get(bot_id) or {}
    return sell_stats(sells, arch, None)


def _raw_totals(rows, bot_id):
    """The same math over the RAW un-rotated ledger (the pre-rotation truth)."""
    sells = [t for t in rows if t.get("bot_id") == bot_id
             and t.get("type") == "sell"
             and "cancelled on restart" not in (t.get("reason") or "")]
    return sell_stats(sells, None, None)


def _fixture_rows():
    """Two bots, old + recent activity, multi-leg positions, wins and losses,
    plus a cancelled-on-restart bookkeeping sell (excluded from stats)."""
    return [
        # b1 / OLDTOK1: old position, 2 sell legs, net WIN (+5 +3)
        _buy("b1", "OLDTOK1", 0.001, 40),
        _sell("b1", "OLDTOK1", 0.001, 5.0, 40, fully_closed=False,
              sell_fraction=0.5),
        _sell("b1", "OLDTOK1", 0.001, 3.0, 39.9, fully_closed=True,
              sell_fraction=0.5),
        # b1 / OLDTOK2: old position, LOSS (-4)
        _buy("b1", "OLDTOK2", 0.002, 35),
        _sell("b1", "OLDTOK2", 0.002, -4.0, 34.9, reason="stop",
              fully_closed=True),
        # b1: old cancelled-on-restart sell — bookkeeping, never counted
        _sell("b1", "OLDTOK2", 0.002, 0.0, 34.8,
              reason="cancelled on restart", fully_closed=True),
        # b1 / NEWTOK: recent WIN (+2)
        _buy("b1", "NEWTOK", 0.003, 1),
        _sell("b1", "NEWTOK", 0.003, 2.0, 0.9, fully_closed=True),
        # b2: old LOSS (-1), recent WIN (+7)
        _buy("b2", "OLDTOK3", 0.004, 30),
        _sell("b2", "OLDTOK3", 0.004, -1.0, 29.9, reason="stop",
              fully_closed=True),
        _buy("b2", "NEWTOK2", 0.005, 2),
        _sell("b2", "NEWTOK2", 0.005, 7.0, 1.9, fully_closed=True),
    ]


# ---------------------------------------------------------------------------
# 1) Rotation identity: leaderboard totals identical before/after rotation
# ---------------------------------------------------------------------------

def test_rotation_leaderboard_totals_identical(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_APPEND_MODE", "on")
    monkeypatch.setenv("LEDGER_ROTATE_DAYS", "21")
    rows = _fixture_rows()
    _write_base(tmp_path, rows)

    pre = {b: _raw_totals(rows, b) for b in ("b1", "b2")}

    store = MultiBotTradeStore(data_dir=tmp_path)
    post = {b: _lb_totals(store, b) for b in ("b1", "b2")}

    for b in ("b1", "b2"):
        assert post[b][0] == pytest.approx(pre[b][0]), f"{b} total_pnl drifted"
        assert post[b][1] == pre[b][1], f"{b} position count drifted"
        assert post[b][2] == pre[b][2], f"{b} win count drifted"
    # sanity: the expected numbers themselves
    assert pre["b1"] == (pytest.approx(6.0), 3, 2)   # +8, -4, +2
    assert pre["b2"] == (pytest.approx(6.0), 2, 1)   # -1, +7

    # rotation actually happened: old rows left the base, archive holds them
    base = json.loads((tmp_path / "trades_multi.json").read_text())
    assert all(_iso(21) < (t.get("time") or "") for t in base)
    arch_lines = [json.loads(ln) for ln in
                  (tmp_path / "trades_multi_archive.jsonl")
                  .read_text().splitlines() if ln.strip()]
    assert len(arch_lines) + len(base) == len(rows)

    # stable across a SECOND reboot (stats re-derived from the archive)
    store2 = MultiBotTradeStore(data_dir=tmp_path)
    for b in ("b1", "b2"):
        assert _lb_totals(store2, b)[0] == pytest.approx(pre[b][0])
        assert _lb_totals(store2, b)[1:] == pre[b][1:]


def test_rotation_crash_leftover_never_double_counts(tmp_path, monkeypatch):
    """Simulate a crash between the archive append and the base rewrite: the
    archived rows are STILL in the base at next boot. Signature dedup must
    drop them from the base without re-counting (totals identical, archive
    gains no effective duplicates)."""
    monkeypatch.setenv("LEDGER_APPEND_MODE", "on")
    monkeypatch.setenv("LEDGER_ROTATE_DAYS", "21")
    rows = _fixture_rows()
    _write_base(tmp_path, rows)
    pre = {b: _raw_totals(rows, b) for b in ("b1", "b2")}

    store = MultiBotTradeStore(data_dir=tmp_path)
    store.load_trades()

    # crash replay: shove ALL original rows back into the base
    _write_base(tmp_path, rows)
    store2 = MultiBotTradeStore(data_dir=tmp_path)
    for b in ("b1", "b2"):
        got = _lb_totals(store2, b)
        assert got[0] == pytest.approx(pre[b][0]), f"{b} double-counted"
        assert got[1:] == pre[b][1:]
    # base is clean again (old rows deduped out, recent rows kept once)
    base = json.loads((tmp_path / "trades_multi.json").read_text())
    recent = [t for t in rows if (t.get("time") or "") > _iso(21)]
    assert len(base) == len(recent)


def test_rotation_respects_bot_reset_after_iso(tmp_path, monkeypatch):
    """Adversarial review r2 F1: a bot with a dashboard re-baseline
    (bot_state reset_after_iso) whose OLD history spans the reset. The
    dashboard drops the bot's pre-reset rows per-row; the archived aggregate
    must exclude them too, or rotation folds the PRE-reset P&L back into
    /api/leaderboard. Identity is checked the dashboard's way (per-row reset
    filter on active sells + archive fold with reset_after_iso)."""
    monkeypatch.setenv("LEDGER_APPEND_MODE", "on")
    monkeypatch.setenv("LEDGER_ROTATE_DAYS", "21")
    reset_iso = _iso(35)   # re-baseline between the two OLD positions
    rows = [
        # pre-reset OLD position (+100): must NOT reach the archived aggregate
        _buy("b1", "PRERESET", 0.01, 40),
        _sell("b1", "PRERESET", 0.01, 100.0, 40, fully_closed=True),
        # post-reset OLD position (+10): archived AND counted
        _buy("b1", "POSTRESET", 0.02, 30),
        _sell("b1", "POSTRESET", 0.02, 10.0, 30, fully_closed=True),
        # recent position (+2): stays in the base
        _buy("b1", "NEWTOK", 0.03, 1),
        _sell("b1", "NEWTOK", 0.03, 2.0, 0.9, fully_closed=True),
    ]
    _write_base(tmp_path, rows)
    (tmp_path / "bot_state").mkdir(exist_ok=True)
    (tmp_path / "bot_state" / "b1.json").write_text(json.dumps(
        {"bot_id": "b1", "reset_after_iso": reset_iso}))

    # the un-rotated leaderboard truth: per-row reset filter, no fold
    raw_sells = [t for t in rows if t.get("type") == "sell"
                 and (t.get("time") or "") >= reset_iso]
    pre = sell_stats(raw_sells, None, None)
    assert pre == (pytest.approx(12.0), 2, 2)   # +10 +2; the +100 is pre-reset

    store = MultiBotTradeStore(data_dir=tmp_path)
    store.load_trades()   # trigger boot compaction/rotation

    arch = (store.load_rotation_stats().get("bots") or {}).get("b1") or {}
    assert arch.get("pnl") == pytest.approx(10.0), (
        "archived aggregate must exclude pre-reset rows")
    assert arch.get("positions") == 1 and arch.get("wins") == 1

    active_sells = [t for t in store.load_trades(bot_id="b1")
                    if t.get("type") == "sell"
                    and (t.get("time") or "") >= reset_iso]
    post = sell_stats(active_sells, arch, reset_iso)
    assert post[0] == pytest.approx(pre[0]), "reset bot total_pnl drifted"
    assert post[1:] == pre[1:]


def test_rotation_no_straddle_group_stays(tmp_path, monkeypatch):
    """A (bot_id, token) group with ANY recent row is kept whole in the base —
    position joins (leaderboard groups, restore_positions) never split."""
    monkeypatch.setenv("LEDGER_APPEND_MODE", "on")
    monkeypatch.setenv("LEDGER_ROTATE_DAYS", "21")
    rows = [
        _buy("b1", "STRAD", 0.01, 40),
        _sell("b1", "STRAD", 0.01, 1.0, 40, fully_closed=False,
              sell_fraction=0.5),
        _sell("b1", "STRAD", 0.01, 2.0, 1, fully_closed=True,
              sell_fraction=0.5),      # recent leg -> whole group stays
        _buy("b1", "GONE", 0.02, 40),
        _sell("b1", "GONE", 0.02, -1.0, 39.9, fully_closed=True),
    ]
    _write_base(tmp_path, rows)
    store = MultiBotTradeStore(data_dir=tmp_path)
    store.load_trades()   # trigger lazy boot compaction/rotation
    base_toks = [t["token"] for t in
                 json.loads((tmp_path / "trades_multi.json").read_text())]
    assert base_toks.count("STRAD") == 3
    assert "GONE" not in base_toks
    got = _lb_totals(store, "b1")
    assert got == (pytest.approx(2.0), 2, 1)  # STRAD +3 win, GONE -1 loss


def test_rotation_protects_open_position_tokens(tmp_path, monkeypatch):
    """bot_state open_positions (holdings truth) are never archived, even when
    every ledger row for the token is old."""
    monkeypatch.setenv("LEDGER_APPEND_MODE", "on")
    monkeypatch.setenv("LEDGER_ROTATE_DAYS", "21")
    rows = [_buy("b1", "HODL", 0.01, 40)]  # old, unsold
    _write_base(tmp_path, rows)
    (tmp_path / "bot_state").mkdir(exist_ok=True)
    (tmp_path / "bot_state" / "b1.json").write_text(json.dumps({
        "bot_id": "b1", "balance_usd": 100.0, "in_flight_usd": 0.0,
        "open_positions": [{"token": "HODL", "entry_price": 0.01}],
    }))
    MultiBotTradeStore(data_dir=tmp_path).load_trades()
    base = json.loads((tmp_path / "trades_multi.json").read_text())
    assert [t["token"] for t in base] == ["HODL"]
    assert not (tmp_path / "trades_multi_archive.jsonl").exists()


def test_rotation_keeps_unparseable_times(tmp_path, monkeypatch):
    """Rows with missing/unparseable times are never archived (fail-safe)."""
    monkeypatch.setenv("LEDGER_APPEND_MODE", "on")
    monkeypatch.setenv("LEDGER_ROTATE_DAYS", "21")
    rows = [
        {"type": "buy", "bot_id": "b1", "token": "T", "time": "t1"},
        {"type": "buy", "bot_id": "b1", "token": "U"},  # no time at all
    ]
    _write_base(tmp_path, rows)
    store = MultiBotTradeStore(data_dir=tmp_path)
    assert len(store.load_trades()) == 2
    assert not (tmp_path / "trades_multi_archive.jsonl").exists()


def test_rotation_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_APPEND_MODE", "on")
    monkeypatch.setenv("LEDGER_ROTATE_DAYS", "0")
    rows = _fixture_rows()
    _write_base(tmp_path, rows)
    store = MultiBotTradeStore(data_dir=tmp_path)
    assert len(store.load_trades()) == len(rows)
    assert not (tmp_path / "trades_multi_archive.jsonl").exists()
    assert store.load_rotation_stats() == {}


def test_rotation_fail_open_loads_everything(tmp_path, monkeypatch):
    """Any rotation error (here: the archive path is a DIRECTORY, so streaming
    it raises) must fall back to loading the FULL ledger — never lose rows,
    never raise into the boot path."""
    monkeypatch.setenv("LEDGER_APPEND_MODE", "on")
    monkeypatch.setenv("LEDGER_ROTATE_DAYS", "21")
    rows = _fixture_rows()
    _write_base(tmp_path, rows)
    (tmp_path / "trades_multi_archive.jsonl").mkdir()
    store = MultiBotTradeStore(data_dir=tmp_path)
    assert len(store.load_trades()) == len(rows)
    for b in ("b1", "b2"):
        assert _lb_totals(store, b)[0] == pytest.approx(_raw_totals(rows, b)[0])


# ---------------------------------------------------------------------------
# 2) Boot re-derivation: TODAY's rows always survive rotation
# ---------------------------------------------------------------------------

def test_rotation_preserves_todays_rows_for_daily_rederive(tmp_path, monkeypatch):
    """The per-bot daily circuit breaker re-derives TODAY's pnl from the ledger
    at boot (dip_scanner boot path). Every today-row must survive rotation."""
    monkeypatch.setenv("LEDGER_APPEND_MODE", "on")
    monkeypatch.setenv("LEDGER_ROTATE_DAYS", "21")
    rows = _fixture_rows() + [
        _buy("b1", "TODAY1", 0.01, 0.01),
        _sell("b1", "TODAY1", 0.01, -8.5, 0.005, reason="stop",
              fully_closed=True),
        _sell("b1", "NEWTOK", 0.003, 1.5, 0.001, fully_closed=True),
    ]
    _write_base(tmp_path, rows)
    store = MultiBotTradeStore(data_dir=tmp_path)
    today = datetime.now(timezone.utc).date().isoformat()
    want = [t for t in rows if (t.get("time") or "").startswith(today)]
    got = [t for t in store.load_trades()
           if (t.get("time") or "").startswith(today)]
    assert len(got) == len(want)

    def _daily(ts):  # mirror the boot re-derive: today's b1 sell pnl sum
        return sum(float(t.get("pnl") or 0) for t in ts
                   if t.get("type") == "sell" and t.get("bot_id") == "b1")
    assert _daily(got) == pytest.approx(_daily(want))
    # the two injected today-rows (-8.5, +1.5) are definitely in there; the
    # fixture NEWTOK sell (+2.0, ~0.9d ago) may or may not be same-UTC-day.
    assert _daily(got) in (pytest.approx(-7.0), pytest.approx(-5.0))


# ---------------------------------------------------------------------------
# 3) entry_meta cache trim
# ---------------------------------------------------------------------------

def test_meta_trim_window_and_disk_lossless(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_APPEND_MODE", "on")
    monkeypatch.setenv("LEDGER_ROTATE_DAYS", "0")   # isolate the trim
    monkeypatch.setenv("LEDGER_META_KEEP_ROWS", "5")
    fat = {"pc_h6": -3.0, "liq_usd": 50000.0, "junk": "x" * 100,
           "daily_halt_would_block": True, "reentry_cap_would_block": False}
    rows = [_buy("b1", f"T{i}", 0.001 * (i + 1), 10 - i, meta=dict(fat))
            for i in range(12)]
    _write_base(tmp_path, rows)
    store = MultiBotTradeStore(data_dir=tmp_path)
    loaded = store.load_trades()
    assert len(loaded) == 12
    old, new = loaded[:-5], loaded[-5:]
    for t in new:
        assert t["entry_meta"] == fat, "newest rows must keep FULL meta"
    for t in old:
        em = t["entry_meta"]
        assert em.get("_meta_trimmed") is True
        # live_faithful whitelist survives; the fat keys are gone
        assert em["daily_halt_would_block"] is True
        assert em["reentry_cap_would_block"] is False
        assert "junk" not in em and "liq_usd" not in em
    # DISK stays lossless — the trim is cache-only
    disk = json.loads((tmp_path / "trades_multi.json").read_text())
    assert all(t["entry_meta"] == fat for t in disk)


def test_meta_trim_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_APPEND_MODE", "on")
    monkeypatch.setenv("LEDGER_ROTATE_DAYS", "0")
    monkeypatch.setenv("LEDGER_META_KEEP_ROWS", "0")
    fat = {"a": 1, "b": 2, "c": 3, "d": 4}
    rows = [_buy("b1", f"T{i}", 0.001, 5, meta=dict(fat)) for i in range(8)]
    _write_base(tmp_path, rows)
    loaded = MultiBotTradeStore(data_dir=tmp_path).load_trades()
    assert all(t["entry_meta"] == fat for t in loaded)


# ---------------------------------------------------------------------------
# 4) Streamed compaction write
# ---------------------------------------------------------------------------

def test_streamed_write_roundtrip(tmp_path):
    rows = [{"type": "buy", "token": "ünïcode", "pnl": 1.5,
             "entry_meta": {"k": [1, 2, {"n": None}]}},
            {"type": "sell", "token": "B", "pnl": -0.25}]
    p = tmp_path / "arr.json"
    MultiBotTradeStore._atomic_write_stream(p, rows)
    assert json.loads(p.read_text()) == rows
    MultiBotTradeStore._atomic_write_stream(p, [])
    assert json.loads(p.read_text()) == []
    assert not (tmp_path / "arr.json.tmp").exists()


def test_boot_compaction_streamed_no_loss_no_dup(tmp_path, monkeypatch):
    """The sidecar fold (now streamed) still round-trips exactly."""
    monkeypatch.setenv("LEDGER_APPEND_MODE", "on")
    monkeypatch.setenv("LEDGER_ROTATE_DAYS", "0")
    s1 = MultiBotTradeStore(data_dir=tmp_path)
    for i in range(6):
        s1.record_trade(_buy("b1", f"S{i}", 0.001, 0.001), bot_id="b1")
    s2 = MultiBotTradeStore(data_dir=tmp_path)   # restart -> compaction
    loaded = s2.load_trades()
    assert sorted(t["token"] for t in loaded) == [f"S{i}" for i in range(6)]
    base = json.loads((tmp_path / "trades_multi.json").read_text())
    assert len(base) == 6
    assert (tmp_path / "trades_multi.jsonl").read_text().strip() == ""


# ---------------------------------------------------------------------------
# 5) sell_stats fold semantics
# ---------------------------------------------------------------------------

def test_sell_stats_reset_after_iso_skips_stale_archive():
    sells = [{"token": "A", "entry_price": 1.0, "pnl": 2.0}]
    arch = {"pnl": 10.0, "positions": 3, "wins": 2,
            "latest_time": "2026-06-01T00:00:00+00:00"}
    # no reset: fold
    assert sell_stats(sells, arch, None) == (pytest.approx(12.0), 4, 3)
    # reset NEWER than every archived row: archived history is pre-reset -> skip
    assert sell_stats(sells, arch, "2026-07-01T00:00:00+00:00") == (
        pytest.approx(2.0), 1, 1)
    # reset OLDER than the newest archived row: fold (rotation already
    # excluded pre-reset rows at rotation time)
    assert sell_stats(sells, arch, "2026-05-01T00:00:00+00:00") == (
        pytest.approx(12.0), 4, 3)
    # no archive at all
    assert sell_stats(sells, None, None) == (pytest.approx(2.0), 1, 1)


def test_trade_sig_distinguishes_same_second_ladder_legs():
    """Adversarial review r2: two REAL same-second sell legs with identical
    size/pnl (TP ladder split fills) must never collide in the crash-recovery
    dedup signature — a collision silently drops one leg from the base at the
    next boot. A true crash duplicate is byte-identical (same reason too)."""
    from core.multi_bot_persistence import _trade_sig
    leg = {"bot_id": "b1", "time": "2026-07-01T10:00:00+00:00", "type": "sell",
           "token": "X", "entry_price": 1.0, "pnl": 5.0, "pnl_pct": 5.0,
           "amount_usd": 10.0, "reason": "tp1 slice"}
    twin = dict(leg, reason="tp2 slice")
    assert _trade_sig(leg) != _trade_sig(twin)
    assert _trade_sig(leg) == _trade_sig(dict(leg))   # true duplicate matches
