"""Backfill bot_id='baseline_v1' on legacy trades.json records.

Idempotent: re-running is safe (records that already have a bot_id are
left untouched). Backs up the original file on first run.
"""
import argparse
import json
from pathlib import Path


def migrate(data_dir: Path) -> int:
    trades_path = data_dir / "trades.json"
    if not trades_path.exists():
        print(f"No trades.json in {data_dir}; nothing to do.")
        return 0
    data = json.loads(trades_path.read_text())
    updated = 0
    for t in data:
        if "bot_id" not in t:
            t["bot_id"] = "baseline_v1"
            updated += 1
    if updated:
        backup = data_dir / "trades.json.pre-migrate"
        if not backup.exists():
            backup.write_text(trades_path.read_text())
            print(f"Backup saved to {backup}")
        trades_path.write_text(json.dumps(data))
    print(f"Migration complete: {updated} records updated, {len(data)} total")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="/data")
    args = p.parse_args()
    raise SystemExit(migrate(Path(args.data_dir)))
