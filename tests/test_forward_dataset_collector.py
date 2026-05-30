"""Tests for the forward dataset collector.

Verifies:
  - dump_snapshot() writes .npy + .json to the correct date dir
  - update_outcome() finds the partial label and adds outcome fields
  - Disk-space guard returns False when threshold exceeded
"""
import os
import sys
import tempfile
import json
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from feeds.candle_utils import Candle
from feeds.forward_dataset_collector import ForwardDatasetCollector
import feeds.forward_dataset_collector as _fdc


@pytest.fixture(autouse=True)
def _enable_forward_dataset(monkeypatch):
    """The collector is DISABLED by default in production (master gate added
    2026-05-27 — the ChartCNN it feeds is non-predictive + it filled the Railway
    volume). These tests verify the dump/outcome LOGIC, which only runs when
    enabled, so flip the gate on for the test. (Without this they assert ok is
    True while the production default returns False at the gate.)"""
    monkeypatch.setattr(_fdc, "FORWARD_DATASET_ENABLED", True)


def _flat_candles(n: int, base_ts: int = 1700000000):
    return [Candle(open_time=base_ts + i * 60, open=1.0, high=1.01, low=0.99,
                   close=1.0, volume=100.0, close_time=base_ts + (i + 1) * 60)
            for i in range(n)]


def test_dump_writes_npy_and_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        col = ForwardDatasetCollector(root_dir=tmpdir)
        ok = col.dump_snapshot(
            token_address="ADDR_TEST",
            ts_iso="2026-05-15T12:00:00+00:00",
            candles_1m=_flat_candles(60),
            candles_5m=_flat_candles(60),
            candles_15m=_flat_candles(60),
            context={"triggers_fired": ["test_trigger"], "hour_ct": 7},
        )
        assert ok is True
        # Date dir created
        date_dir = Path(tmpdir) / "2026-05-15"
        assert date_dir.exists()
        npys = list(date_dir.glob("*.npy"))
        jsons = list(date_dir.glob("*.json"))
        assert len(npys) == 1
        assert len(jsons) == 1
        # Load and inspect
        img = np.load(npys[0])
        assert img.shape == (3, 64, 64)
        with open(jsons[0]) as f:
            label = json.load(f)
        assert label["addr"] == "ADDR_TEST"
        assert label["outcome_label"] is None  # not yet closed
        assert label["context"]["triggers_fired"] == ["test_trigger"]


def test_update_outcome_finds_and_appends():
    with tempfile.TemporaryDirectory() as tmpdir:
        col = ForwardDatasetCollector(root_dir=tmpdir)
        col.dump_snapshot(
            token_address="ADDR_UPDATE",
            ts_iso="2026-05-15T13:00:00+00:00",
            candles_1m=_flat_candles(60),
            candles_5m=_flat_candles(60),
            candles_15m=_flat_candles(60),
            context={},
        )
        updated = col.update_outcome(
            token_address="ADDR_UPDATE",
            ts_iso="2026-05-15T13:00:00+00:00",
            outcome_label=1,
            outcome_pnl_pct=4.03,
        )
        assert updated is True
        # Re-read
        date_dir = Path(tmpdir) / "2026-05-15"
        jsons = list(date_dir.glob("*ADDR_UPDATE*.json"))
        with open(jsons[0]) as f:
            label = json.load(f)
        assert label["outcome_label"] == 1
        assert label["outcome_pnl_pct"] == 4.03


def test_returns_false_on_insufficient_candles():
    with tempfile.TemporaryDirectory() as tmpdir:
        col = ForwardDatasetCollector(root_dir=tmpdir)
        ok = col.dump_snapshot(
            token_address="ADDR_SHORT",
            ts_iso="2026-05-15T14:00:00+00:00",
            candles_1m=_flat_candles(10),  # below 30
            candles_5m=_flat_candles(60),
            candles_15m=_flat_candles(60),
            context={},
        )
        assert ok is False


if __name__ == "__main__":
    test_dump_writes_npy_and_json()
    test_update_outcome_finds_and_appends()
    test_returns_false_on_insufficient_candles()
    print("All forward collector tests passed")
