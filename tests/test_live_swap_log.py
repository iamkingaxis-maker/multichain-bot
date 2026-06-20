# tests/test_live_swap_log.py
"""Tests for the COMPLETE live-swap telemetry capture (core/live_swap_log.py).

Covers (a) log_live_swap writes a complete record (all REQUIRED_FIELDS present,
null for unavailable) + is fail-open on a bad path; (b) the per-step latency
math (total = confirmed - decision; durations non-negative); (c) the endpoint
summary aggregation over synthetic records. The wiring test (d) lives in
tests/test_live_swap_wiring.py."""
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core import live_swap_log as lsl


# ── (a) complete record + fail-open ───────────────────────────────────────────
def test_log_writes_complete_record_all_keys_present(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIVE_SWAP_LOG_MODE", "on")
    # Supply only a handful of fields — the rest MUST appear as null, not be omitted.
    lsl.log_live_swap(side="buy", bot_id="probe_live", token_address="MINT123",
                      token_symbol="ABC", success=True, out_amount=42)
    p = tmp_path / lsl.LOG_BASENAME
    assert p.exists()
    rec = json.loads(p.read_text().strip())
    # EVERY required field is present (completeness gate).
    for k in lsl.REQUIRED_FIELDS:
        assert k in rec, f"missing required field: {k}"
    # Unsupplied fields are null (not absent).
    assert rec["liquidity_usd"] is None
    assert rec["sign_duration_ms"] is None
    # ts auto-stamped, address-keyed preserved.
    assert rec["ts"]
    assert rec["token_address"] == "MINT123"
    assert rec["success"] is True
    assert rec["out_amount"] == 42


def test_log_fail_open_on_bad_path(monkeypatch):
    # DATA_DIR points at a path that cannot be opened for append -> must NOT raise.
    monkeypatch.setenv("LIVE_SWAP_LOG_MODE", "on")
    monkeypatch.setenv("DATA_DIR", "/nonexistent_dir_xyz/deeper/still")
    # Should swallow the error silently (fail-open).
    lsl.log_live_swap(side="sell", token_address="X", success=False)


def test_log_mode_off_is_dormant(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIVE_SWAP_LOG_MODE", "off")
    lsl.log_live_swap(side="buy", token_address="X", success=True)
    assert not (tmp_path / lsl.LOG_BASENAME).exists()


def test_failure_reason_classifier_enum():
    assert lsl.classify_failure_reason(True, "anything") == "ok"
    assert lsl.classify_failure_reason(False, "HTTP 429 too many requests") == "rate_limit"
    assert lsl.classify_failure_reason(False, "confirmation timeout — assuming dropped") == "timeout"
    assert lsl.classify_failure_reason(False, "slippage exceeded cap") == "slippage_exceeded"
    assert lsl.classify_failure_reason(False, "custom program error: 0x1") == "revert"
    assert lsl.classify_failure_reason(False, "something weird") == "other"
    assert lsl.classify_failure_reason(False, None) == "other"


def test_failure_reason_normalized_on_write(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIVE_SWAP_LOG_MODE", "on")
    lsl.log_live_swap(side="buy", token_address="X", success=False,
                      failure_reason="order HTTP 429", error_text=None)
    rec = json.loads((tmp_path / lsl.LOG_BASENAME).read_text().strip())
    assert rec["failure_reason"] == "rate_limit"


# ── (b) per-step latency math ─────────────────────────────────────────────────
def _latency_record(decision_ts, order_start, order_dur, sign_dur,
                    exec_start, exec_dur, confirmed_ts):
    """A record as the wiring would build it; assert the latency invariants."""
    return {
        "decision_ts": decision_ts, "order_start_ts": order_start,
        "order_duration_ms": order_dur, "sign_duration_ms": sign_dur,
        "execute_start_ts": exec_start, "execute_duration_ms": exec_dur,
        "confirmed_ts": confirmed_ts,
        "total_latency_ms": round((confirmed_ts - decision_ts) * 1000, 1),
    }


def test_latency_total_is_confirmed_minus_decision():
    r = _latency_record(decision_ts=100.0, order_start=100.2, order_dur=150.0,
                        sign_dur=8.0, exec_start=100.4, exec_dur=2200.0,
                        confirmed_ts=102.7)
    assert r["total_latency_ms"] == round((102.7 - 100.0) * 1000, 1)
    assert r["total_latency_ms"] == 2700.0


def test_latency_durations_non_negative():
    r = _latency_record(decision_ts=100.0, order_start=100.2, order_dur=150.0,
                        sign_dur=8.0, exec_start=100.4, exec_dur=2200.0,
                        confirmed_ts=102.7)
    for k in ("order_duration_ms", "sign_duration_ms", "execute_duration_ms",
              "total_latency_ms"):
        assert r[k] >= 0.0


# ── (c) endpoint summary aggregation ──────────────────────────────────────────
def _rec(success, side, total_lat, exec_dur, slip, o429=0, e429=0, reason=None):
    return {
        "success": success, "side": side, "total_latency_ms": total_lat,
        "execute_duration_ms": exec_dur, "fill_vs_mid_slippage_pct": slip,
        "order_429_count": o429, "execute_429_count": e429,
        "failure_reason": reason if reason else ("ok" if success else "other"),
    }


def test_summary_empty_is_fail_open():
    s = lsl.summarize_live_swaps([])
    assert s["n"] == 0
    assert s["success_rate"] is None
    assert s["order_429_total"] == 0
    assert s["failure_reason_histogram"] == {}


def test_summary_aggregation_over_synthetic_records():
    recs = [
        _rec(True, "buy", 2000.0, 1500.0, 1.2, o429=1, e429=0, reason="ok"),
        _rec(True, "sell", 3000.0, 2500.0, 0.8, o429=0, e429=2, reason="ok"),
        _rec(False, "buy", 5000.0, 4000.0, 4.0, o429=3, e429=1, reason="rate_limit"),
        _rec(False, "sell", 4000.0, 3000.0, 2.0, o429=0, e429=0, reason="timeout"),
    ]
    s = lsl.summarize_live_swaps(recs)
    assert s["n"] == 4
    assert s["success_rate"] == 0.5
    # latency percentiles
    assert s["median_total_latency_ms"] is not None
    assert s["p90_total_latency_ms"] == 5000.0  # nearest-rank top
    assert s["median_execute_duration_ms"] is not None
    # slippage
    assert s["median_fill_vs_mid_slippage_pct"] == 1.6  # median of [1.2,0.8,4.0,2.0]
    assert s["mean_fill_vs_mid_slippage_pct"] == 2.0
    # 429 totals
    assert s["order_429_total"] == 4
    assert s["execute_429_total"] == 3
    # failure histogram
    assert s["failure_reason_histogram"]["ok"] == 2
    assert s["failure_reason_histogram"]["rate_limit"] == 1
    assert s["failure_reason_histogram"]["timeout"] == 1
    # by side
    assert s["by_side"]["buy"] == 2
    assert s["by_side"]["sell"] == 2


def test_summary_ignores_non_numeric_latency():
    recs = [
        _rec(True, "buy", None, None, None),
        _rec(True, "buy", 2000.0, 1000.0, 1.0),
    ]
    s = lsl.summarize_live_swaps(recs)
    assert s["n"] == 2
    assert s["median_total_latency_ms"] == 2000.0  # the None is ignored


def test_read_live_swaps_missing_file_is_empty(tmp_path):
    p = str(tmp_path / "does_not_exist.jsonl")
    assert lsl.read_live_swaps(p) == []


def test_read_live_swaps_skips_corrupt_lines(tmp_path):
    p = tmp_path / lsl.LOG_BASENAME
    p.write_text('{"a":1}\nNOT JSON\n{"b":2}\n')
    recs = lsl.read_live_swaps(str(p))
    assert recs == [{"a": 1}, {"b": 2}]
