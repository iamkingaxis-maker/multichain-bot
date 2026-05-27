"""One-shot: clear the CNN forward dataset from the volume.

/data/cnn_dataset/forward held ~2.5GB (194k .npy chart renders) — the
forward_dataset_collector dumped one per candidate per scan cycle to train the
ChartCNN, which is NOT gated on in live trading (validated non-predictive). The
collector is now disabled by default (ENABLE_FORWARD_DATASET), so this dataset is
pure dead weight. This removes it once (sentinel-guarded).

SAFE: the enforced rug filter (filter_cluster_19_rug) loads its model from the
in-repo models/ dir (chart_encoder_v1.pt), NOT from /data/cnn_dataset — so this
does not affect any live trading decision.

Run modes:
  - In-container boot (real clear):   python scripts/cleanup_cnn_dataset.py /data
  - Dry run (report only):            python scripts/cleanup_cnn_dataset.py /data --dry-run
  - Local self-test:                  python scripts/cleanup_cnn_dataset.py --selftest
"""
from __future__ import annotations
import json
import shutil
import sys
from pathlib import Path

SENTINEL = ".cnn_dataset_cleared_v1"
TARGET = "cnn_dataset"


def cleanup(data_dir, dry_run: bool = False) -> dict:
    data_dir = Path(data_dir)
    sentinel = data_dir / SENTINEL
    if sentinel.exists():
        return {"skipped": "sentinel exists"}
    target = data_dir / TARGET
    existed = target.is_dir()
    if existed and not dry_run:
        shutil.rmtree(target, ignore_errors=True)
    summary = {"target": str(target), "existed": existed}
    if not dry_run:
        sentinel.write_text(json.dumps(summary))
    return summary


def _selftest():
    import tempfile
    d = Path(tempfile.mkdtemp())
    # a populated cnn_dataset (nested files) + unrelated dirs that must survive
    (d / "cnn_dataset" / "forward" / "2026-05-27").mkdir(parents=True)
    (d / "cnn_dataset" / "forward" / "2026-05-27" / "x.npy").write_text("img")
    (d / "cnn_dataset" / "forward" / "buys").mkdir(parents=True)
    (d / "universe_recorder").mkdir()
    (d / "trades_multi.json").write_text("[]")
    out = cleanup(d)
    print("SELFTEST summary:", json.dumps(out, indent=2))
    assert out["existed"] is True, "should report it existed"
    assert not (d / "cnn_dataset").exists(), "cnn_dataset not removed"
    assert (d / "universe_recorder").exists(), "unrelated dir wrongly removed"
    assert (d / "trades_multi.json").exists(), "live ledger wrongly removed"
    assert "skipped" in cleanup(d), "not idempotent"
    # a cnn_dataset recreated later (collector re-enabled) is left alone
    (d / "cnn_dataset").mkdir()
    assert "skipped" in cleanup(d) and (d / "cnn_dataset").exists(), "future dataset wrongly cleared"
    print("SELFTEST PASS: cnn_dataset cleared, unrelated/live kept, idempotent, future-safe.")
    shutil.rmtree(d)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        dd = next((a for a in sys.argv[1:] if not a.startswith("--")), "/data")
        print(json.dumps(cleanup(Path(dd), dry_run="--dry-run" in sys.argv), indent=2))
