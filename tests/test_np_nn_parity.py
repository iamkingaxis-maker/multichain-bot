"""Primitive parity: core/np_nn.py vs torch equivalents.

Each pure-numpy primitive must match its torch counterpart to < 1e-4 max
abs diff on random inputs. These are the building blocks of the chart
encoder (ENFORCED live rug filter) and chart CNN (shadow), so the
backbone numerics must be tight before model-level parity.

Requires torch installed (dev env only). The runtime never imports torch.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

torch = pytest.importorskip("torch")
import torch.nn.functional as F  # noqa: E402

from core import np_nn  # noqa: E402

RNG = np.random.default_rng(1234)
TOL = 1e-4


def _maxdiff(a, b):
    return float(np.max(np.abs(np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64))))


def test_conv2d_3x3_pad1_parity():
    for c_in, c_out, h, w in [(3, 16, 64, 64), (16, 32, 32, 32), (48, 64, 8, 8)]:
        x = RNG.standard_normal((c_in, h, w)).astype(np.float32)
        wt = RNG.standard_normal((c_out, c_in, 3, 3)).astype(np.float32)
        b = RNG.standard_normal((c_out,)).astype(np.float32)
        out_np = np_nn.conv2d_3x3_pad1(x, wt, b)
        out_t = F.conv2d(torch.from_numpy(x).unsqueeze(0), torch.from_numpy(wt),
                         torch.from_numpy(b), stride=1, padding=1)[0].numpy()
        assert out_np.shape == out_t.shape
        assert _maxdiff(out_np, out_t) < TOL, _maxdiff(out_np, out_t)


def test_batchnorm2d_infer_parity():
    c, h, w = 32, 16, 16
    x = RNG.standard_normal((c, h, w)).astype(np.float32)
    gamma = RNG.standard_normal((c,)).astype(np.float32)
    beta = RNG.standard_normal((c,)).astype(np.float32)
    mean = RNG.standard_normal((c,)).astype(np.float32)
    var = (RNG.random((c,)).astype(np.float32) + 0.1)
    out_np = np_nn.batchnorm2d_infer(x, gamma, beta, mean, var, eps=1e-5)
    bn = torch.nn.BatchNorm2d(c, eps=1e-5)
    with torch.no_grad():
        bn.weight.copy_(torch.from_numpy(gamma))
        bn.bias.copy_(torch.from_numpy(beta))
        bn.running_mean.copy_(torch.from_numpy(mean))
        bn.running_var.copy_(torch.from_numpy(var))
    bn.eval()
    with torch.no_grad():
        out_t = bn(torch.from_numpy(x).unsqueeze(0))[0].numpy()
    assert _maxdiff(out_np, out_t) < TOL, _maxdiff(out_np, out_t)


def test_relu_parity():
    x = RNG.standard_normal((16, 8, 8)).astype(np.float32)
    out_np = np_nn.relu(x)
    out_t = F.relu(torch.from_numpy(x)).numpy()
    assert _maxdiff(out_np, out_t) < TOL


def test_maxpool2d_k2_parity():
    for c, h, w in [(3, 64, 64), (32, 16, 16), (64, 8, 8)]:
        x = RNG.standard_normal((c, h, w)).astype(np.float32)
        out_np = np_nn.maxpool2d_k2(x)
        out_t = F.max_pool2d(torch.from_numpy(x).unsqueeze(0), kernel_size=2)[0].numpy()
        assert out_np.shape == out_t.shape
        assert _maxdiff(out_np, out_t) < TOL


def test_linear_parity():
    for n_in, n_out in [(1024, 128), (128, 64), (128, 15), (128, 1)]:
        x = RNG.standard_normal((n_in,)).astype(np.float32)
        wt = RNG.standard_normal((n_out, n_in)).astype(np.float32)
        b = RNG.standard_normal((n_out,)).astype(np.float32)
        out_np = np_nn.linear(x, wt, b)
        out_t = F.linear(torch.from_numpy(x), torch.from_numpy(wt), torch.from_numpy(b)).numpy()
        assert _maxdiff(out_np, out_t) < TOL, _maxdiff(out_np, out_t)


def test_softmax_parity():
    x = RNG.standard_normal((15,)).astype(np.float32)
    out_np = np_nn.softmax(x)
    out_t = torch.softmax(torch.from_numpy(x), dim=0).numpy()
    assert _maxdiff(out_np, out_t) < TOL


def test_sigmoid_parity():
    x = RNG.standard_normal((8,)).astype(np.float32)
    out_np = np_nn.sigmoid(x)
    out_t = torch.sigmoid(torch.from_numpy(x)).numpy()
    assert _maxdiff(out_np, out_t) < TOL
