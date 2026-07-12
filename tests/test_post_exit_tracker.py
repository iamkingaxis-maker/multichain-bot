# tests/test_post_exit_tracker.py — Solana post-exit tail tracker (2026-07-10)
"""Pure-helper coverage for core/post_exit_tracker.py (queue/due/result rows,
bounded JSONL files) + source-level wiring guards on feeds/dip_scanner.py
(queue on FULL close, sweep spawned in run(), off-loop file I/O) in the
test_realtime_dip_detection.py style."""
import json

import pytest

from core import post_exit_tracker as pet


class TestQueueRow:
    def test_fields_and_due_ts(self):
        r = pet.queue_row(bot_id="badday_young_rt_paper", token="TOK",
                          address="MintAddr", exit_price=0.0012,
                          exit_pnl_pct=7.3456789, exit_kind="TP2",
                          close_ts=1000.0)
        assert r["bot_id"] == "badday_young_rt_paper"
        assert r["token"] == "TOK" and r["address"] == "MintAddr"
        assert r["exit_price"] == 0.0012
        assert r["exit_pnl_pct"] == 7.3457
        assert r["exit_kind"] == "TP2"
        assert r["close_ts"] == 1000.0
        assert r["due_ts"] == 1000.0 + 6 * 3600.0

    def test_none_safe(self):
        r = pet.queue_row(bot_id=None, token=None, address=None,
                          exit_price=None, exit_pnl_pct=None, exit_kind=None,
                          close_ts=None)
        assert r["address"] == "" and r["exit_price"] == 0.0
        assert r["due_ts"] == pet.DUE_DELAY_SECS


class TestDueRows:
    def test_split(self):
        rows = [{"due_ts": 100.0, "t": "a"}, {"due_ts": 300.0, "t": "b"},
                {"due_ts": 200.0, "t": "c"}]
        due, keep = pet.due_rows(rows, now=200.0)
        assert [r["t"] for r in due] == ["a", "c"]
        assert [r["t"] for r in keep] == ["b"]

    def test_malformed_due_ts_counts_as_due(self):
        due, keep = pet.due_rows([{"due_ts": "garbage"}], now=0.0)
        assert len(due) == 1 and keep == []


class TestResultRow:
    def test_vs_exit_pct(self):
        pending = pet.queue_row("b", "TOK", "addr", 1.0, 5.0, "TP1", 0.0)
        out = pet.result_row(pending, post_price=1.2, checked_ts=99.0)
        assert out["post6h_price"] == 1.2
        assert out["post6h_vs_exit_pct"] == pytest.approx(20.0)
        assert out["died"] is False
        assert out["checked_ts"] == 99.0
        assert out["bot_id"] == "b"          # pending fields carried through

    def test_unpriceable_is_died(self):
        pending = pet.queue_row("b", "TOK", "addr", 1.0, 5.0, "TP1", 0.0)
        for px in (None, 0.0, "junk"):
            out = pet.result_row(pending, post_price=px, checked_ts=1.0)
            assert out["died"] is True
            assert out["post6h_vs_exit_pct"] is None

    def test_zero_exit_price_yields_no_pct(self):
        pending = pet.queue_row("b", "TOK", "addr", 0.0, -99.0, "HARD_STOP", 0.0)
        out = pet.result_row(pending, post_price=1.0, checked_ts=1.0)
        assert out["post6h_vs_exit_pct"] is None and out["died"] is False


class TestFiles:
    def test_read_rows_missing_and_malformed(self, tmp_path):
        p = tmp_path / "pending.jsonl"
        assert pet.read_rows(str(p)) == []
        p.write_text('{"a": 1}\nnot-json\n\n[1,2]\n{"b": 2}\n', encoding="utf-8")
        rows = pet.read_rows(str(p))
        assert rows == [{"a": 1}, {"b": 2}]     # malformed + non-dict skipped

    def test_append_bounded_oldest_first(self, tmp_path):
        p = str(tmp_path / "pending.jsonl")
        for i in range(8):
            pet.append_row(p, {"i": i}, cap=5)
        rows = pet.read_rows(p)
        assert [r["i"] for r in rows] == [3, 4, 5, 6, 7]  # newest 5 kept

    def test_rewrite_rows_atomic_replace(self, tmp_path):
        p = str(tmp_path / "pending.jsonl")
        pet.append_row(p, {"i": 0})
        pet.rewrite_rows(p, [{"i": 9}])
        assert pet.read_rows(p) == [{"i": 9}]

    def test_paths_respect_data_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DATA_DIR", str(tmp_path))
        assert pet.pending_path().startswith(str(tmp_path))
        assert pet.pending_path().endswith("post_exit_pending.jsonl")
        assert pet.results_path().startswith(str(tmp_path))
        assert pet.results_path().endswith("post_exit_results.jsonl")


class TestTrackMode:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("POST_EXIT_TRACK_MODE", raising=False)
        assert pet.track_mode_on() is True

    def test_off_variants(self, monkeypatch):
        for v in ("off", "0", "false", "no", " OFF "):
            monkeypatch.setenv("POST_EXIT_TRACK_MODE", v)
            assert pet.track_mode_on() is False
        monkeypatch.setenv("POST_EXIT_TRACK_MODE", "on")
        assert pet.track_mode_on() is True


class TestScannerWiring:
    """Source-level guards (test_realtime_dip_detection.py style) — the queue
    must ride the FULL-close bookkeeping and the sweep must spawn in run()."""

    def test_full_close_queues_pending(self):
        import inspect
        import feeds.dip_scanner as ds
        # _execute_bot_sell is now a per-position serialization shim
        # (adversarial review r2, 2026-07-12); the sell body — and this
        # wiring — lives in _execute_bot_sell_inner. Guard the body, plus
        # the shim's delegation so the body is actually reachable.
        shim = inspect.getsource(ds.DipScanner._execute_bot_sell)
        assert "_execute_bot_sell_inner" in shim, \
            "_execute_bot_sell no longer delegates to the sell body"
        src = inspect.getsource(ds.DipScanner._execute_bot_sell_inner)
        assert "post_exit_tracker" in src, \
            "_execute_bot_sell lost the post-exit queue wiring"
        i = src.index("post_exit_tracker")
        guard = src[:i].rindex("result.fully_closed")
        assert guard > 0, "post-exit queue must be gated on result.fully_closed"
        assert "to_thread" in src[i:i + 800], \
            "post-exit queue write must run off-loop (to_thread)"

    def test_sweep_spawned_in_run(self):
        import inspect
        import feeds.dip_scanner as ds
        src = inspect.getsource(ds.DipScanner.run)
        assert "_post_exit_sweep_loop" in src, "run() no longer spawns the sweep"

    def test_sweep_is_fail_open_and_off_loop(self):
        import inspect
        import feeds.dip_scanner as ds
        src = inspect.getsource(ds.DipScanner._post_exit_sweep_loop)
        assert "track_mode_on" in src
        assert "_fast_batch_prices" in src, "sweep lost the batched price path"
        assert src.count("to_thread") >= 3, \
            "sweep file I/O must run off-loop (read, append, rewrite)"
        assert "except Exception" in src, "sweep must swallow exceptions"
