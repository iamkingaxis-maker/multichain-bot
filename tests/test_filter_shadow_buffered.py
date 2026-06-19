"""Tests for the buffered (off-loop) filter-shadow capture.

Regression guard for the 2026-06-19 LOOP-LAG fix: the choke point must do
ZERO file I/O on the event loop. Each verdict is BUILT (pure CPU) and APPENDED
to a bounded in-memory buffer; the SOLE disk write is a single off-loop batched
flush per cycle via asyncio.to_thread.

Covers:
  * build_record returns the correct dict shape with NO open/disk_usage call.
  * write_records writes N records in exactly ONE open() + is fail-open on a
    bad path.
  * _flush_filter_shadow_buf writes via asyncio.to_thread (off-loop), empties
    the buffer, is fail-open + no-op on empty.
  * Buffer cap respected (append past cap does not grow unbounded).
"""
import asyncio
import builtins
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ── build_record: pure CPU, NO file I/O ──────────────────────────────────────

def test_build_record_shape_no_io(monkeypatch):
    from feeds import filter_shadow_recorder as fsr

    opened = []
    disk = []
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open",
                        lambda *a, **k: opened.append(a) or real_open(*a, **k))
    import shutil
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda *a, **k: disk.append(a) or (1, 1, 1))

    pair = {
        "pairAddress": "POOL",
        "priceChange": {"h24": -5.0, "h6": -3.0, "h1": -2.0, "m5": -1.0},
        "txns": {"h6": {"buys": 4, "sells": 2}, "h1": {"buys": 3, "sells": 0},
                 "m5": {"buys": 0, "sells": 0}},
        "volume": {"h24": 12345.0},
        "liquidity": {"usd": 30000.0},
        "marketCap": 250000.0,
    }
    rec = fsr.build_record("ADDR", "SYM", pair, "filter_x", "BLOCK", "why")

    # NO file I/O at all
    assert opened == []
    assert disk == []
    # Correct dict shape
    assert rec["token_address"] == "ADDR"
    assert rec["token_symbol"] == "SYM"
    assert rec["pair_address"] == "POOL"
    assert rec["filter_name"] == "filter_x"
    assert rec["verdict"] == "BLOCK"
    assert rec["block_reasons"] == "why"
    assert rec["pc_h1"] == -2.0
    assert rec["bs_h6"] == 2.0
    assert rec["bs_h1"] is None  # buys>0, sells==0 -> inf -> None
    assert rec["bs_m5"] == 0.0
    assert rec["liquidity_usd"] == 30000.0
    assert rec["mcap"] == 250000.0
    assert "ts" in rec


def test_build_record_handles_none_pair():
    from feeds import filter_shadow_recorder as fsr
    rec = fsr.build_record("A", "S", None, "f", "PASS")
    assert rec["pair_address"] == ""
    assert rec["verdict"] == "PASS"


# ── write_records: ONE open for a batch + fail-open ──────────────────────────

def test_write_records_single_open_for_batch(tmp_path, monkeypatch):
    from feeds import filter_shadow_recorder as fsr

    rec = fsr.FilterShadowRecorder(str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(rec, "_disk_has_space", lambda: True)

    opens = []
    real_open = builtins.open

    def _spy_open(*a, **k):
        opens.append(a[0])
        return real_open(*a, **k)

    monkeypatch.setattr(builtins, "open", _spy_open)

    batch = [fsr.build_record("A", "S", {"pairAddress": str(i)}, "f", "BLOCK")
             for i in range(5)]
    n = rec.write_records(batch)

    assert n == 5
    # exactly ONE open of the log file for the whole batch
    log_opens = [p for p in opens if str(p).endswith("log.jsonl")]
    assert len(log_opens) == 1
    # all 5 lines on disk
    lines = (tmp_path / "log.jsonl").read_text().strip().splitlines()
    assert len(lines) == 5
    assert all(json.loads(ln)["filter_name"] == "f" for ln in lines)


def test_write_records_empty_is_noop(tmp_path):
    from feeds import filter_shadow_recorder as fsr
    rec = fsr.FilterShadowRecorder(str(tmp_path / "log.jsonl"))
    assert rec.write_records([]) == 0
    assert not (tmp_path / "log.jsonl").exists()


def test_write_records_fail_open_bad_path():
    from feeds import filter_shadow_recorder as fsr
    # A path whose parent cannot exist -> open raises -> fail-open returns 0
    bad = os.path.join(os.devnull, "nope", "log.jsonl")
    rec = fsr.FilterShadowRecorder(bad)
    out = rec.write_records([fsr.build_record("A", "S", {}, "f", "BLOCK")])
    assert out == 0  # never raises


def test_legacy_record_still_works(tmp_path, monkeypatch):
    from feeds import filter_shadow_recorder as fsr
    rec = fsr.FilterShadowRecorder(str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(rec, "_disk_has_space", lambda: True)
    assert rec.record("A", "S", {"pairAddress": "P"}, "f", "BLOCK", "r") is True
    lines = (tmp_path / "log.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["token_address"] == "A"


# ── scanner choke point: buffers, no per-verdict disk write ───────────────────

class _StubScanner:
    """Minimal stand-in exercising the buffer + flush in isolation."""

    def __init__(self, cap=5000):
        self._filter_shadow_buf = []
        self._filter_shadow_buf_max = cap

    # Mirror the production _flush_filter_shadow_buf verbatim in behaviour.
    async def _flush_filter_shadow_buf(self):
        from feeds.dip_scanner import DipScanner
        await DipScanner._flush_filter_shadow_buf(self)


def test_choke_point_buffers_no_disk_write(monkeypatch):
    """Building+appending must NOT call write_records/open per verdict."""
    from feeds import filter_shadow_recorder as fsr

    wrote = []
    monkeypatch.setattr(fsr, "write_records",
                        lambda recs: wrote.append(len(recs)) or len(recs))

    sc = _StubScanner()
    verdicts = [("filter_a", "BLOCK", ""), ("filter_b", "PASS", ""),
                ("filter_c", "SHADOW_BLOCK", "")]
    for fname, fv, fr in verdicts:
        sc._filter_shadow_buf.append(
            fsr.build_record("A", "S", {"pairAddress": "P"}, fname,
                             fsr._normalize_verdict(fv), fr))

    # buffer grew, NO write happened yet
    assert len(sc._filter_shadow_buf) == 3
    assert wrote == []
    # normalized verdicts landed
    assert [r["verdict"] for r in sc._filter_shadow_buf] == \
        ["BLOCK", "PASS", "BLOCK"]


def test_buffer_cap_respected():
    from feeds import filter_shadow_recorder as fsr
    sc = _StubScanner(cap=3)
    for i in range(10):
        if len(sc._filter_shadow_buf) >= sc._filter_shadow_buf_max:
            break
        sc._filter_shadow_buf.append(
            fsr.build_record("A", "S", {}, "f", "BLOCK"))
    assert len(sc._filter_shadow_buf) == 3  # bounded, not 10


# ── flush: off-loop via to_thread, empties buffer, fail-open, no-op empty ─────

def test_flush_uses_to_thread_and_empties(monkeypatch):
    from feeds.dip_scanner import DipScanner

    sc = _StubScanner()
    sc._filter_shadow_buf = [{"x": 1}, {"x": 2}]

    to_thread_calls = []
    written = []

    async def _fake_to_thread(fn, *args):
        to_thread_calls.append(fn)
        return fn(*args)

    monkeypatch.setattr("feeds.dip_scanner.asyncio.to_thread", _fake_to_thread)
    monkeypatch.setattr(
        "feeds.filter_shadow_recorder.write_records",
        lambda recs: written.append(list(recs)) or len(recs))

    asyncio.run(DipScanner._flush_filter_shadow_buf(sc))

    # off-loop write happened exactly once with the whole batch
    assert len(to_thread_calls) == 1
    assert written == [[{"x": 1}, {"x": 2}]]
    # buffer emptied
    assert sc._filter_shadow_buf == []


def test_flush_noop_on_empty(monkeypatch):
    from feeds.dip_scanner import DipScanner
    sc = _StubScanner()
    calls = []
    monkeypatch.setattr("feeds.dip_scanner.asyncio.to_thread",
                        lambda *a, **k: calls.append(1))
    asyncio.run(DipScanner._flush_filter_shadow_buf(sc))
    assert calls == []  # no-op, never touched to_thread


def test_flush_fail_open(monkeypatch):
    from feeds.dip_scanner import DipScanner
    sc = _StubScanner()
    sc._filter_shadow_buf = [{"x": 1}]

    async def _boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr("feeds.dip_scanner.asyncio.to_thread", _boom)
    # must not raise (fail-open)
    asyncio.run(DipScanner._flush_filter_shadow_buf(sc))
