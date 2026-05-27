"""Tests for the universe-recorder events.jsonl retention cap (2026-05-27 volume
fix) and the one-shot phantom-backup cleanup."""
import json
from pathlib import Path

from scripts.universe_dip_recorder import rotate_events_if_needed
from scripts.cleanup_phantom_backups import cleanup


def _write_events(path: Path, n: int):
    with path.open("w") as f:
        for i in range(n):
            f.write(json.dumps({"i": i, "pad": "x" * 200}) + "\n")


def test_rotation_noop_under_cap(tmp_path):
    ev = tmp_path / "events.jsonl"
    _write_events(ev, 100)
    before = ev.read_text()
    assert rotate_events_if_needed(ev, max_mb=10.0) is False
    assert ev.read_text() == before  # untouched


def test_rotation_drops_oldest_half_over_cap(tmp_path):
    ev = tmp_path / "events.jsonl"
    _write_events(ev, 10000)  # ~2MB at ~210 bytes/line
    assert ev.stat().st_size / 1e6 > 0.5
    assert rotate_events_if_needed(ev, max_mb=0.5) is True
    lines = [json.loads(l) for l in ev.read_text().splitlines()]
    # kept the most-recent half — first surviving index is ~5000, last is 9999
    assert lines[-1]["i"] == 9999
    assert lines[0]["i"] == 5000
    assert len(lines) == 5000


def test_rotation_missing_file_is_safe(tmp_path):
    assert rotate_events_if_needed(tmp_path / "nope.jsonl", max_mb=0.1) is False


def test_cleanup_removes_known_backups_only(tmp_path):
    (tmp_path / "backup_giga_1").mkdir()
    (tmp_path / "backup_eurc_2").mkdir()
    (tmp_path / "universe_recorder").mkdir()
    (tmp_path / "trades_multi.json").write_text("[]")
    out = cleanup(tmp_path)
    assert set(out["removed"]) == {"backup_giga_1", "backup_eurc_2"}
    assert (tmp_path / "universe_recorder").exists()
    assert (tmp_path / "trades_multi.json").exists()


def test_cleanup_idempotent_and_future_safe(tmp_path):
    (tmp_path / "backup_giga_1").mkdir()
    cleanup(tmp_path)
    # sentinel set → a backup created later is left alone
    (tmp_path / "backup_eurc_future").mkdir()
    assert "skipped" in cleanup(tmp_path)
    assert (tmp_path / "backup_eurc_future").exists()
