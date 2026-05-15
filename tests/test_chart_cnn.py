"""Tests for the ChartCNN model architecture.

Verifies:
  - Forward pass produces correct output shapes (pattern logits + outcome logit)
  - Model loads/saves cleanly via state_dict
  - CPU inference time within budget (under 100ms per call after warmup)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch
import numpy as np

from models.chart_cnn import ChartCNN, NUM_PATTERN_CLASSES


def test_forward_pass_shapes():
    model = ChartCNN()
    model.eval()
    x = torch.zeros(1, 3, 64, 64)
    with torch.no_grad():
        pattern_logits, outcome_logit = model(x)
    assert pattern_logits.shape == (1, NUM_PATTERN_CLASSES)
    assert outcome_logit.shape == (1, 1)


def test_batch_forward_pass():
    model = ChartCNN()
    model.eval()
    x = torch.randn(8, 3, 64, 64)
    with torch.no_grad():
        p, o = model(x)
    assert p.shape == (8, NUM_PATTERN_CLASSES)
    assert o.shape == (8, 1)


def test_state_dict_roundtrip(tmp_path=None):
    import tempfile
    model1 = ChartCNN()
    model2 = ChartCNN()
    with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as f:
        torch.save(model1.state_dict(), f.name)
        sd = torch.load(f.name, map_location="cpu", weights_only=True)
        model2.load_state_dict(sd)
    # Verify parameters match
    for p1, p2 in zip(model1.parameters(), model2.parameters()):
        assert torch.equal(p1, p2)


def test_inference_latency_budget():
    model = ChartCNN()
    model.eval()
    x = torch.randn(1, 3, 64, 64)
    # warmup
    with torch.no_grad():
        for _ in range(3):
            model(x)
    # measure
    t0 = time.perf_counter()
    with torch.no_grad():
        for _ in range(10):
            model(x)
    avg_ms = (time.perf_counter() - t0) * 100  # 10 calls → avg in ms
    print(f"avg inference latency: {avg_ms:.1f}ms")
    assert avg_ms < 100, f"inference too slow: {avg_ms}ms"


if __name__ == "__main__":
    test_forward_pass_shapes()
    test_batch_forward_pass()
    test_state_dict_roundtrip()
    test_inference_latency_budget()
    print("All ChartCNN tests passed")
