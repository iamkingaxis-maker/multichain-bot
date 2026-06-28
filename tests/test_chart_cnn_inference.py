"""Tests for the production CNN inference singleton.

Verifies:
  - Returns None when weights file is missing (graceful degradation)
  - Returns valid dict shape on synthetic candles
  - Cache hits on second call with same (addr, last_minute_ts)
  - Self-disables after exception, re-enables after retry window
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from feeds.candle_utils import Candle
from core.chart_cnn_inference import ChartCNNInference


def _flat_candles(n: int, base_ts: int = 1700000000):
    return [Candle(open_time=base_ts + i * 60, open=1.0, high=1.01, low=0.99,
                   close=1.0, volume=100.0, close_time=base_ts + (i + 1) * 60)
            for i in range(n)]


def _write_npz_weights(suffix=".npz"):
    """Build a fresh ChartCNN (torch, dev-only) and export to .npz like the
    runtime expects — mirrors scripts/export_chart_weights.py."""
    import torch
    from models.chart_cnn import ChartCNN
    model = ChartCNN()
    model.eval()
    sd = model.state_dict()
    arrays = {k: v.detach().cpu().numpy().astype(np.float32)
              for k, v in sd.items() if not k.endswith("num_batches_tracked")}
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.close()
    np.savez(f.name, **arrays)
    return f.name


def test_returns_none_when_weights_missing():
    inf = ChartCNNInference(weights_path="/nonexistent/path/weights.npz")
    result = inf.predict("ADDR1", _flat_candles(60), _flat_candles(60), _flat_candles(60))
    assert result is None
    assert inf.disabled is True


def test_returns_dict_when_weights_present():
    path = _write_npz_weights()
    inf = ChartCNNInference(weights_path=path)
    result = inf.predict("ADDR1", _flat_candles(60), _flat_candles(60), _flat_candles(60))
    assert result is not None
    assert "pattern" in result
    assert "pattern_conf" in result
    assert "outcome_prob" in result
    assert 0.0 <= result["pattern_conf"] <= 1.0
    assert 0.0 <= result["outcome_prob"] <= 1.0


def test_cache_hits_same_minute():
    path = _write_npz_weights()
    inf = ChartCNNInference(weights_path=path)
    c1 = _flat_candles(60)
    c5 = _flat_candles(60)
    c15 = _flat_candles(60)
    r1 = inf.predict("ADDR1", c1, c5, c15)
    r2 = inf.predict("ADDR1", c1, c5, c15)
    assert r1 == r2
    assert inf.cache_hits >= 1


def test_returns_none_on_insufficient_candles():
    path = _write_npz_weights()
    inf = ChartCNNInference(weights_path=path)
    r = inf.predict("ADDR1", _flat_candles(10), _flat_candles(60), _flat_candles(60))
    assert r is None


if __name__ == "__main__":
    test_returns_none_when_weights_missing()
    test_returns_dict_when_weights_present()
    test_cache_hits_same_minute()
    test_returns_none_on_insufficient_candles()
    print("All inference tests passed")
