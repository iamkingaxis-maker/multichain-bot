"""SP5: atomic cutover from baseline_v1 <- champion_proposal.

Backs up the current baseline to baseline_v1.json.pre-cutover-<timestamp>,
then writes champion's field values into baseline_v1.json (preserving
bot_id='baseline_v1' to keep trade history continuous).

Marks champion_proposal.json enabled=false.

Usage:
  python scripts/sp5_cutover.py            # dry-run; prints diff, no writes
  python scripts/sp5_cutover.py --confirm  # actually performs the cutover
"""
from __future__ import annotations
import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def build_new_baseline(old_baseline: dict, champion: dict) -> dict:
    """Construct the new baseline_v1 config: champion fields, baseline identity."""
    new_baseline = dict(champion)
    new_baseline["bot_id"] = "baseline_v1"
    new_baseline["enabled"] = True
    today = datetime.now(timezone.utc).date().isoformat()
    new_baseline["display_name"] = f"Baseline (post-cutover {today})"
    return new_baseline


def perform_cutover(baseline_path: Path, champion_path: Path, confirm: bool):
    """Execute the cutover. Returns (backup_path, new_baseline_dict)."""
    old_baseline = json.loads(baseline_path.read_text())
    champion = json.loads(champion_path.read_text())
    new_baseline = build_new_baseline(old_baseline, champion)
    if not confirm:
        print("DRY-RUN - no files modified. Would write:")
        print(json.dumps(new_baseline, indent=2, sort_keys=True))
        return None, new_baseline
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    backup_path = baseline_path.with_suffix(f".json.pre-cutover-{timestamp}")
    shutil.copy2(baseline_path, backup_path)
    print(f"Backed up baseline to {backup_path}")
    baseline_path.write_text(json.dumps(new_baseline, indent=2, sort_keys=True))
    print(f"Wrote new baseline to {baseline_path}")
    champion["enabled"] = False
    champion_path.write_text(json.dumps(champion, indent=2, sort_keys=True))
    print(f"Disabled champion at {champion_path}")
    return backup_path, new_baseline


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--confirm", action="store_true",
                   help="Actually perform the cutover (default is dry-run).")
    args = p.parse_args()
    project_root = Path(__file__).parent.parent
    baseline_path = project_root / "config" / "bots" / "baseline_v1.json"
    champion_path = project_root / "config" / "bots" / "champion_proposal.json"
    if not baseline_path.exists():
        print(f"ERROR: {baseline_path} not found")
        return 1
    if not champion_path.exists():
        print(f"ERROR: {champion_path} not found - run scripts/sp4_champion_synthesis.py first")
        return 1
    backup_path, _new_baseline = perform_cutover(baseline_path, champion_path, confirm=args.confirm)
    if args.confirm:
        print("\nNext step: git add config/bots/*.json && git commit && railway up --detach")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
