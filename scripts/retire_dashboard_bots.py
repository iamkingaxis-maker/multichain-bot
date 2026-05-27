"""One-shot: clear retired bots' state files from the dashboard (2026-05-27).

14 dead single-signal strategy variants + converged duplicate sweeps were
disabled (enabled:false) to declutter the fleet. Their bot_state/{id}.json
snapshots linger, so the dashboard (which globs bot_state/) still shows them.
This deletes those snapshots so the retired bots vanish from the dashboard.
Configs are KEPT (disabled) for reversibility — this only removes the runtime
snapshot. Disabled bots aren't re-instantiated (BotManager skips enabled=False),
so they won't recreate state.

Idempotent, sentinel-guarded, backs up first. Run as a boot hook (before bots
load) like the other migrations.

Run modes:
  - In-container boot:  python scripts/retire_dashboard_bots.py /data
  - Dry run:            python scripts/retire_dashboard_bots.py /data --dry-run
  - Local self-test:    python scripts/retire_dashboard_bots.py --selftest
"""
from __future__ import annotations
import json
import shutil
import sys
import time
from pathlib import Path

SENTINEL = ".dashboard_retire_v1"
RETIRED = [
    # dead single-signal strategy variants (lose, teach nothing)
    "one_sec_only", "one_sec_no_filters", "flow_only", "chart_pattern_only",
    "cnn_cluster_only", "runner_capture", "champ_runner", "champ_sniper",
    # converged duplicate sweeps (keep one of each family, cut the twins)
    "compound_linear", "compound_winners_only", "psych_h24_100", "psych_h24_150",
    "bleed_120min", "reentry_60m",
]


def retire(data_dir, dry_run: bool = False) -> dict:
    data_dir = Path(data_dir)
    sentinel = data_dir / SENTINEL
    if sentinel.exists():
        return {"skipped": "sentinel exists"}
    bs = data_dir / "bot_state"
    removed: list = []
    if bs.is_dir():
        bk = data_dir / f"backup_retire_{int(time.time())}"
        for bid in RETIRED:
            p = bs / f"{bid}.json"
            if p.exists():
                if not dry_run:
                    bk.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, bk / f"{bid}.json")
                    p.unlink()
                removed.append(bid)
    summary = {"removed": removed, "n": len(removed)}
    if not dry_run:
        sentinel.write_text(json.dumps(summary))
    return summary


def _selftest():
    import tempfile
    d = Path(tempfile.mkdtemp())
    (d / "bot_state").mkdir()
    for bid in ("flow_only", "champ_sniper", "keep_me"):
        (d / "bot_state" / f"{bid}.json").write_text("{}")
    out = retire(d)
    assert set(out["removed"]) == {"flow_only", "champ_sniper"}, out
    assert (d / "bot_state" / "keep_me.json").exists(), "wrongly removed a keeper"
    assert not (d / "bot_state" / "flow_only.json").exists(), "did not remove"
    assert "skipped" in retire(d), "not idempotent"
    print("SELFTEST PASS:", json.dumps(out))
    shutil.rmtree(d)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        dd = next((a for a in sys.argv[1:] if not a.startswith("--")), "/data")
        print(json.dumps(retire(Path(dd), dry_run="--dry-run" in sys.argv), indent=2))
