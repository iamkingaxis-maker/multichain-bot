import json
import subprocess
import sys
from pathlib import Path


def test_migration_adds_bot_id_to_legacy_records(tmp_path):
    legacy = [
        {"type": "buy", "token": "A", "time": "t1"},
        {"type": "sell", "token": "A", "time": "t2", "pnl": 1.0},
    ]
    trades_file = tmp_path / "trades.json"
    trades_file.write_text(json.dumps(legacy))
    script = Path(__file__).parent.parent / "scripts" / "migrate_trades_json_bot_id.py"
    result = subprocess.run(
        [sys.executable, str(script), "--data-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(trades_file.read_text())
    assert all(t["bot_id"] == "baseline_v1" for t in data)


def test_migration_is_idempotent(tmp_path):
    records = [{"type": "buy", "token": "A", "bot_id": "custom"}]
    (tmp_path / "trades.json").write_text(json.dumps(records))
    script = Path(__file__).parent.parent / "scripts" / "migrate_trades_json_bot_id.py"
    subprocess.run([sys.executable, str(script), "--data-dir", str(tmp_path)], check=True)
    subprocess.run([sys.executable, str(script), "--data-dir", str(tmp_path)], check=True)
    data = json.loads((tmp_path / "trades.json").read_text())
    assert data[0]["bot_id"] == "custom"


def test_migration_creates_backup_on_first_run(tmp_path):
    (tmp_path / "trades.json").write_text(json.dumps([{"type": "buy", "token": "A"}]))
    script = Path(__file__).parent.parent / "scripts" / "migrate_trades_json_bot_id.py"
    subprocess.run([sys.executable, str(script), "--data-dir", str(tmp_path)], check=True)
    assert (tmp_path / "trades.json.pre-migrate").exists()


def test_migration_no_op_when_file_missing(tmp_path):
    script = Path(__file__).parent.parent / "scripts" / "migrate_trades_json_bot_id.py"
    result = subprocess.run(
        [sys.executable, str(script), "--data-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
