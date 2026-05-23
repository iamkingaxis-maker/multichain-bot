import json
import pytest
from pathlib import Path
from scripts.sp5_rollback import find_latest_backup, restore_from_backup


def test_find_latest_backup_picks_most_recent(tmp_path):
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True)
    (bots_dir / "baseline_v1.json.pre-cutover-2026-05-20-100000").write_text("v1")
    (bots_dir / "baseline_v1.json.pre-cutover-2026-05-25-120000").write_text("v2")
    (bots_dir / "baseline_v1.json.pre-cutover-2026-05-22-130000").write_text("v3")
    latest = find_latest_backup(bots_dir / "baseline_v1.json")
    assert latest.name.endswith("2026-05-25-120000")


def test_find_latest_backup_returns_none_when_no_backups(tmp_path):
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True)
    latest = find_latest_backup(bots_dir / "baseline_v1.json")
    assert latest is None


def test_restore_from_backup_overwrites_baseline(tmp_path):
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True)
    baseline_path = bots_dir / "baseline_v1.json"
    baseline_path.write_text('{"version": "new"}')
    backup_path = bots_dir / "baseline_v1.json.pre-cutover-2026-05-25-120000"
    backup_path.write_text('{"version": "old"}')
    restore_from_backup(baseline_path, backup_path)
    restored = baseline_path.read_text()
    assert restored == '{"version": "old"}'


def test_restore_from_backup_returns_pre_rollback_hash(tmp_path):
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True)
    baseline_path = bots_dir / "baseline_v1.json"
    baseline_path.write_text('{"version": "new"}')
    backup_path = bots_dir / "baseline_v1.json.pre-cutover-2026-05-25-120000"
    backup_path.write_text('{"version": "old"}')
    pre_hash = restore_from_backup(baseline_path, backup_path)
    import hashlib
    expected = hashlib.sha256(b'{"version": "new"}').hexdigest()
    assert pre_hash == expected
