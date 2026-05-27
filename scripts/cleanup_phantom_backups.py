"""One-shot: remove the 2026-05-27 phantom-scrub backup dirs from the volume.

The GIGA and EURC phantom scrubs each wrote a full backup (both trade ledgers +
bot_state/) to /data/backup_giga_<ts>/ and /data/backup_eurc_<ts>/ before
repricing. Both scrubs are verified-good (balances confirmed corrected), so the
backups are pure redundancy now — and the Railway volume hit 80% on 2026-05-27.
This removes ONLY those two known backup prefixes, ONCE (sentinel-guarded), so it
can never delete a FUTURE scrub's backup.

Run modes:
  - In-container boot (real cleanup):   python scripts/cleanup_phantom_backups.py /data
  - Dry run (report only):              python scripts/cleanup_phantom_backups.py /data --dry-run
  - Local self-test:                    python scripts/cleanup_phantom_backups.py --selftest
"""
from __future__ import annotations
import json
import shutil
import sys
from pathlib import Path

SENTINEL = ".phantom_backups_cleaned_v1"
PREFIXES = ("backup_giga_", "backup_eurc_")


def cleanup(data_dir, dry_run: bool = False) -> dict:
    data_dir = Path(data_dir)
    sentinel = data_dir / SENTINEL
    if sentinel.exists():
        return {"skipped": "sentinel exists"}
    removed = []
    for p in sorted(data_dir.glob("*")):
        if p.is_dir() and p.name.startswith(PREFIXES):
            if not dry_run:
                shutil.rmtree(p, ignore_errors=True)
            removed.append(p.name)
    summary = {"removed": removed, "count": len(removed)}
    if not dry_run:
        sentinel.write_text(json.dumps(summary))
    return summary


def _selftest():
    import tempfile
    d = Path(tempfile.mkdtemp())
    # two scrub backups (should go) + an unrelated dir + the live ledger (must stay)
    (d / "backup_giga_111").mkdir()
    (d / "backup_giga_111" / "trades_multi.json").write_text("[]")
    (d / "backup_eurc_222").mkdir()
    (d / "universe_recorder").mkdir()           # unrelated — must survive
    (d / "trades_multi.json").write_text("[]")  # live ledger — must survive
    out = cleanup(d)
    print("SELFTEST summary:", json.dumps(out, indent=2))
    assert set(out["removed"]) == {"backup_giga_111", "backup_eurc_222"}, "wrong removal set"
    assert not (d / "backup_giga_111").exists() and not (d / "backup_eurc_222").exists(), "backups not removed"
    assert (d / "universe_recorder").exists(), "unrelated dir wrongly removed"
    assert (d / "trades_multi.json").exists(), "live ledger wrongly removed"
    assert "skipped" in cleanup(d), "not idempotent"
    # a NEW scrub backup created AFTER the sentinel must be left untouched
    (d / "backup_eurc_999").mkdir()
    assert "skipped" in cleanup(d) and (d / "backup_eurc_999").exists(), "future backup wrongly touched"
    print("SELFTEST PASS: known backups removed, unrelated/live kept, idempotent, future-backup safe.")
    shutil.rmtree(d)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        dd = next((a for a in sys.argv[1:] if not a.startswith("--")), "/data")
        print(json.dumps(cleanup(Path(dd), dry_run="--dry-run" in sys.argv), indent=2))
