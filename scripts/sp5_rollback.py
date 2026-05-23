"""SP5: rollback baseline_v1 to a backup.

Usage:
  python scripts/sp5_rollback.py
  python scripts/sp5_rollback.py --backup 2026-05-25-120000
"""
from __future__ import annotations
import argparse
import hashlib
import shutil
from pathlib import Path
from typing import Optional


def find_latest_backup(baseline_path: Path) -> Optional[Path]:
    """Find the most recent baseline_v1.json.pre-cutover-* backup."""
    parent = baseline_path.parent
    stem = baseline_path.stem
    backups = sorted(parent.glob(f"{stem}.json.pre-cutover-*"))
    return backups[-1] if backups else None


def restore_from_backup(baseline_path: Path, backup_path: Path) -> str:
    """Restore baseline from backup. Returns SHA256 of pre-rollback baseline."""
    pre_content = baseline_path.read_bytes()
    pre_hash = hashlib.sha256(pre_content).hexdigest()
    shutil.copy2(backup_path, baseline_path)
    return pre_hash


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--backup",
                   help="Specific backup timestamp. Default: most recent backup.")
    args = p.parse_args()
    project_root = Path(__file__).parent.parent
    baseline_path = project_root / "config" / "bots" / "baseline_v1.json"
    if args.backup:
        backup_path = project_root / "config" / "bots" / f"baseline_v1.json.pre-cutover-{args.backup}"
        if not backup_path.exists():
            print(f"ERROR: {backup_path} not found")
            return 1
    else:
        backup_path = find_latest_backup(baseline_path)
        if backup_path is None:
            print(f"ERROR: no backups found in {baseline_path.parent}")
            return 1
    print(f"Will restore from: {backup_path}")
    print(f"Current baseline: {baseline_path}")
    confirm = input("Proceed with rollback? [y/N]: ")
    if confirm.lower() != "y":
        print("Aborted.")
        return 1
    pre_hash = restore_from_backup(baseline_path, backup_path)
    print(f"Restored baseline from {backup_path}")
    print(f"Pre-rollback baseline hash: {pre_hash[:16]}...")
    print(f"\nNext step: git add config/bots/baseline_v1.json && git commit && railway up --detach")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
