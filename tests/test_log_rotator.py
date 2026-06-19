"""Tests for core/log_rotator.py — the SAFE telemetry-log rotator.

Critical guarantees under test:
  - tail_bytes keeps the LAST keep_bytes aligned to a newline (no partial first line)
  - rotate_file shrinks oversized files keeping the LAST lines; small files untouched
  - rotate_all ONLY touches allowlisted telemetry basenames — trade state is safe
  - RotatorConfig.from_env defaults
"""

import os

import core.log_rotator as lr


def _write(path, text):
    # Binary write so byte sizes are exact on Windows (no \n -> \r\n inflation).
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def test_tail_bytes_aligns_to_newline(tmp_path):
    p = tmp_path / "telemetry.jsonl"
    lines = [f"line-{i:04d}\n" for i in range(1000)]
    _write(str(p), "".join(lines))
    # keep ~ last 100 bytes; each line is 9 bytes ("line-NNNN\n")
    tail = lr.tail_bytes(str(p), 100)
    text = tail.decode("utf-8")
    # No partial first line: must start cleanly at a "line-" token.
    assert text.startswith("line-")
    # The very last line must be preserved in full.
    assert text.rstrip("\n").endswith("line-0999")
    # Every retained line is a complete line (no truncated fragment).
    for ln in text.splitlines():
        assert ln.startswith("line-") and len(ln) == 9


def test_tail_bytes_whole_file_when_keep_exceeds_size(tmp_path):
    p = tmp_path / "telemetry.jsonl"
    _write(str(p), "a\nb\nc\n")
    tail = lr.tail_bytes(str(p), 10_000)
    assert tail == b"a\nb\nc\n"


def test_rotate_file_shrinks_and_keeps_last_lines(tmp_path):
    p = tmp_path / "position_ticks.jsonl"
    # 100KB of distinct lines.
    lines = [f"tick-{i:06d}\n" for i in range(8000)]  # ~88KB
    _write(str(p), "".join(lines))
    size_before = os.path.getsize(str(p))
    assert size_before > 50_000

    freed = lr.rotate_file(str(p), max_bytes=10_000, keep_bytes=5_000)
    assert freed > 0

    size_after = os.path.getsize(str(p))
    # Shrunk to roughly keep_bytes (allow a small alignment slack).
    assert size_after <= 5_000 + 64

    with open(str(p), "rb") as f:
        content = f.read().decode("utf-8")
    # Last line preserved.
    assert content.rstrip("\n").endswith("tick-007999")
    # Early lines gone.
    assert "tick-000000\n" not in content
    # No partial first line.
    assert content.startswith("tick-")
    # No leftover tmp file.
    assert not os.path.exists(str(p) + ".tmp")


def test_rotate_file_small_file_untouched(tmp_path):
    p = tmp_path / "follow_signals.jsonl"
    _write(str(p), "small\n")
    freed = lr.rotate_file(str(p), max_bytes=10_000, keep_bytes=5_000)
    assert freed == 0
    with open(str(p), "rb") as f:
        assert f.read() == b"small\n"


def test_rotate_file_missing_file_returns_zero(tmp_path):
    p = tmp_path / "does_not_exist.jsonl"
    assert lr.rotate_file(str(p), max_bytes=10, keep_bytes=5) == 0


def test_rotate_all_only_touches_allowlisted_names(tmp_path):
    big = "x" * 200  # per line
    big_blob = "".join(f"{big}-{i:05d}\n" for i in range(2000))  # ~400KB

    # A trade STATE file with an allowlist-illegal name — must be untouched.
    trades = tmp_path / "trades_multi.json"
    _write(str(trades), big_blob)
    trades_size_before = os.path.getsize(str(trades))

    # An allowlisted telemetry log — should be rotated.
    ticks = tmp_path / "position_ticks.jsonl"
    _write(str(ticks), big_blob)
    ticks_size_before = os.path.getsize(str(ticks))

    result = lr.rotate_all(str(tmp_path), max_mb=0.01, keep_mb=0.005)  # 10KB/5KB

    # trades_multi.json must NOT appear in results and must be byte-identical.
    assert "trades_multi.json" not in result
    assert os.path.getsize(str(trades)) == trades_size_before
    with open(str(trades), "rb") as f:
        assert len(f.read()) == trades_size_before

    # position_ticks.jsonl was rotated.
    assert result.get("position_ticks.jsonl", 0) > 0
    assert os.path.getsize(str(ticks)) < ticks_size_before


def test_rotate_all_ignores_nonexistent_allowlisted(tmp_path):
    # Empty dir — nothing exists, result empty, no crash.
    result = lr.rotate_all(str(tmp_path), max_mb=80, keep_mb=40)
    assert result == {}


def test_trades_files_not_in_allowlist():
    # Defense-in-depth: trade state must be impossible to rotate.
    assert "trades_multi.json" not in lr._TELEMETRY_LOGS
    assert "trades.json" not in lr._TELEMETRY_LOGS
    assert "bot_state" not in lr._TELEMETRY_LOGS
    assert "closed_positions.csv" not in lr._TELEMETRY_LOGS
    assert "meta_sensor_state.json" not in lr._TELEMETRY_LOGS
    # The known telemetry logs ARE present.
    assert "position_ticks.jsonl" in lr._TELEMETRY_LOGS
    assert "filter_shadow_log.jsonl" in lr._TELEMETRY_LOGS
    assert "signal_events.jsonl" in lr._TELEMETRY_LOGS


def test_config_from_env_defaults(monkeypatch):
    for k in ("LOG_ROTATE_MODE", "LOG_ROTATE_MAX_MB",
              "LOG_ROTATE_KEEP_MB", "LOG_ROTATE_INTERVAL_SECS"):
        monkeypatch.delenv(k, raising=False)
    cfg = lr.RotatorConfig.from_env()
    assert cfg.mode == "on"
    assert cfg.max_mb == 80
    assert cfg.keep_mb == 40
    assert cfg.interval_secs == 3600


def test_config_from_env_overrides(monkeypatch):
    monkeypatch.setenv("LOG_ROTATE_MODE", "off")
    monkeypatch.setenv("LOG_ROTATE_MAX_MB", "120")
    monkeypatch.setenv("LOG_ROTATE_KEEP_MB", "60")
    monkeypatch.setenv("LOG_ROTATE_INTERVAL_SECS", "900")
    cfg = lr.RotatorConfig.from_env()
    assert cfg.mode == "off"
    assert cfg.max_mb == 120
    assert cfg.keep_mb == 60
    assert cfg.interval_secs == 900
