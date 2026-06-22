"""Unit tests for core/paper_live_reconcile.py — the 1:1 skip scoreboard.

Focus: the PURE summarize_reconcile aggregator (the brief's behaviors) plus
fail-open guarantees on the logger / reader.
"""
import json
import os

from core.paper_live_reconcile import (
    LOG_BASENAME,
    log_paper_live_decision,
    read_paper_live_reconcile,
    summarize_reconcile,
)


# ── summarize_reconcile (pure) ────────────────────────────────────────────────
def test_summarize_empty_returns_zeroed():
    assert summarize_reconcile([]) == {"n": 0, "paper_only_n": 0, "by_skip_reason": {}}


def test_summarize_none_returns_zeroed():
    assert summarize_reconcile(None) == {"n": 0, "paper_only_n": 0, "by_skip_reason": {}}


def test_summarize_counts_total_records():
    recs = [
        {"paper_took": True, "live_would_take": True, "skip_reason": None},
        {"paper_took": True, "live_would_take": False, "skip_reason": "liq_floor"},
        {"paper_took": False, "live_would_take": False, "skip_reason": "not_allowlisted"},
    ]
    s = summarize_reconcile(recs)
    assert s["n"] == 3


def test_paper_only_n_counts_paper_took_and_live_would_not():
    recs = [
        {"paper_took": True, "live_would_take": False, "skip_reason": "liq_floor"},
        {"paper_took": True, "live_would_take": False, "skip_reason": "rug_bundle"},
        {"paper_took": True, "live_would_take": True, "skip_reason": None},   # both took → not paper-only
        {"paper_took": False, "live_would_take": False, "skip_reason": "x"},  # paper didn't take → not paper-only
    ]
    s = summarize_reconcile(recs)
    assert s["paper_only_n"] == 2


def test_by_skip_reason_histograms_over_paper_only():
    recs = [
        {"paper_took": True, "live_would_take": False, "skip_reason": "liq_floor"},
        {"paper_took": True, "live_would_take": False, "skip_reason": "liq_floor"},
        {"paper_took": True, "live_would_take": False, "skip_reason": "rug_bundle"},
        # the following are NOT paper-only and must not appear in the histogram:
        {"paper_took": True, "live_would_take": True, "skip_reason": "liq_floor"},
        {"paper_took": False, "live_would_take": False, "skip_reason": "rug_bundle"},
    ]
    s = summarize_reconcile(recs)
    assert s["paper_only_n"] == 3
    assert s["by_skip_reason"] == {"liq_floor": 2, "rug_bundle": 1}


def test_by_skip_reason_missing_reason_buckets_unknown():
    recs = [
        {"paper_took": True, "live_would_take": False},               # no skip_reason key
        {"paper_took": True, "live_would_take": False, "skip_reason": None},
    ]
    s = summarize_reconcile(recs)
    assert s["paper_only_n"] == 2
    assert s["by_skip_reason"] == {"unknown": 2}


def test_summarize_defensive_on_bad_records():
    # Non-dict junk must not raise (fail-open aggregation).
    recs = [None, 123, "nope", {"paper_took": True, "live_would_take": False, "skip_reason": "ok_reason"}]
    s = summarize_reconcile(recs)
    assert s["paper_only_n"] == 1
    assert s["by_skip_reason"] == {"ok_reason": 1}


# ── logger + reader (fail-open) ───────────────────────────────────────────────
def test_log_basename_constant():
    assert LOG_BASENAME == "paper_live_reconcile.jsonl"


def test_log_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PAPER_LIVE_RECONCILE_MODE", "on")
    log_paper_live_decision(
        token_address="MintAbc", token_symbol="FOO",
        paper_took=True, live_would_take=False, skip_reason="liq_floor",
        fresh_source="dexscreener", delta_pct=3.2,
    )
    path = os.path.join(str(tmp_path), LOG_BASENAME)
    recs = read_paper_live_reconcile(path)
    assert len(recs) == 1
    r = recs[0]
    assert r["token_address"] == "MintAbc"
    assert r["paper_took"] is True
    assert r["live_would_take"] is False
    assert r["skip_reason"] == "liq_floor"
    assert r["ts"]  # stamped


def test_log_off_mode_no_io(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PAPER_LIVE_RECONCILE_MODE", "off")
    log_paper_live_decision(
        token_address="MintAbc", token_symbol="FOO",
        paper_took=True, live_would_take=False, skip_reason="liq_floor",
        fresh_source=None, delta_pct=None,
    )
    path = os.path.join(str(tmp_path), LOG_BASENAME)
    assert not os.path.exists(path)


def test_log_none_token_address_becomes_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PAPER_LIVE_RECONCILE_MODE", "on")
    log_paper_live_decision(
        token_address=None, token_symbol="FOO",
        paper_took=True, live_would_take=False, skip_reason="x",
        fresh_source=None, delta_pct=None,
    )
    path = os.path.join(str(tmp_path), LOG_BASENAME)
    recs = read_paper_live_reconcile(path)
    assert recs[0]["token_address"] == ""


def test_read_missing_file_returns_empty():
    assert read_paper_live_reconcile("/no/such/path/xyz.jsonl") == []


def test_read_skips_bad_lines(tmp_path):
    path = tmp_path / LOG_BASENAME
    path.write_text('{"a":1}\nNOT JSON\n\n{"b":2}\n')
    recs = read_paper_live_reconcile(str(path))
    assert recs == [{"a": 1}, {"b": 2}]


def test_log_never_raises_on_bad_data_dir(monkeypatch, tmp_path):
    # Point DATA_DIR at a FILE (so the log path can't be opened as a dir child);
    # the open() must fail and be swallowed — must not raise.
    bad = tmp_path / "iam_a_file"
    bad.write_text("x")
    monkeypatch.setenv("DATA_DIR", str(bad))
    monkeypatch.setenv("PAPER_LIVE_RECONCILE_MODE", "on")
    log_paper_live_decision(
        token_address="x", token_symbol="y",
        paper_took=True, live_would_take=False, skip_reason="z",
        fresh_source=None, delta_pct=None,
    )  # no exception == pass


def test_on_log_rotator_allowlist():
    from core.log_rotator import _TELEMETRY_LOGS
    assert LOG_BASENAME in _TELEMETRY_LOGS
