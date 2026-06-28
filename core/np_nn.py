"""Pure-numpy neural-net primitives — zero torch dependency.

Re-implements the exact forward-pass ops used by the chart CNN / encoder
backbone so the live runtime can drop the ~1GB PyTorch dependency. Every
op mirrors torch's inference-time numerics (float32, cross-correlation
convolution, inference-mode BatchNorm). Parity vs torch is proven by
tests/test_np_nn_parity.py and scripts/validate_chart_parity.py.

Conventions (single-image, no batch dim — inference is batch=1):
  - conv/bn/pool/relu operate on (C, H, W) float32 arrays.
  - linear operates on (N,) or (B, N) float32 arrays.
Conv is cross-correlation (torch convention) — kernels are NOT flipped.
"""
from __future__ import annotations

import numpy as np

EPS_BN = 1e-5


def relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(x, 0.0, dtype=np.float32) if x.dtype == np.float32 else np.maximum(x, 0.0)


def conv2d_3x3_pad1(x: np.ndarray, w: np.ndarray, b: np.ndarray) -> np.ndarray:
    """3x3 conv, padding=1, stride=1, cross-correlation (torch convention).

    x: (C_in, H, W)  w: (C_out, C_in, 3, 3)  b: (C_out,)
    returns (C_out, H, W) float32.

    Uses im2col + a single float32 matmul to mirror torch's conv numerics.
    """
    x = np.asarray(x, dtype=np.float32)
    w = np.asarray(w, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    c_in, h, wid = x.shape
    c_out = w.shape[0]
    # Pad spatial dims by 1 (zeros).
    xp = np.zeros((c_in, h + 2, wid + 2), dtype=np.float32)
    xp[:, 1:1 + h, 1:1 + wid] = x
    # im2col: build (C_in*9, H*W) patch matrix.
    cols = np.empty((c_in * 9, h * wid), dtype=np.float32)
    idx = 0
    for ky in range(3):
        for kx in range(3):
            # patch for this kernel offset over all output positions
            patch = xp[:, ky:ky + h, kx:kx + wid]  # (C_in, H, W)
            cols[idx * c_in:(idx + 1) * c_in, :] = patch.reshape(c_in, h * wid)
            idx += 1
    # Reorder weight to match cols row order: rows grouped by (ky,kx) then c_in.
    # w is (C_out, C_in, 3, 3); transpose to (C_out, 3, 3, C_in) -> (C_out, 9*C_in)
    w_mat = np.transpose(w, (0, 2, 3, 1)).reshape(c_out, 9 * c_in).astype(np.float32)
    out = (w_mat @ cols).astype(np.float32)  # (C_out, H*W)
    out += b[:, None]
    return out.reshape(c_out, h, wid).astype(np.float32)


def batchnorm2d_infer(x: np.ndarray, gamma: np.ndarray, beta: np.ndarray,
                      mean: np.ndarray, var: np.ndarray, eps: float = EPS_BN) -> np.ndarray:
    """Inference BatchNorm2d: (x - mean)/sqrt(var+eps)*gamma + beta, per-channel.

    x: (C, H, W). gamma/beta/mean/var: (C,).
    """
    x = np.asarray(x, dtype=np.float32)
    gamma = np.asarray(gamma, dtype=np.float32)
    beta = np.asarray(beta, dtype=np.float32)
    mean = np.asarray(mean, dtype=np.float32)
    var = np.asarray(var, dtype=np.float32)
    inv_std = (1.0 / np.sqrt(var + np.float32(eps))).astype(np.float32)
    scale = (gamma * inv_std).astype(np.float32)  # (C,)
    shift = (beta - mean * scale).astype(np.float32)  # (C,)
    return (x * scale[:, None, None] + shift[:, None, None]).astype(np.float32)


def maxpool2d_k2(x: np.ndarray) -> np.ndarray:
    """MaxPool2d kernel=2, stride=2 (torch default stride==kernel).

    x: (C, H, W) with even H, W. returns (C, H//2, W//2).
    """
    x = np.asarray(x, dtype=np.float32)
    c, h, wid = x.shape
    h2, w2 = h // 2, wid // 2
    xr = x[:, :h2 * 2, :w2 * 2].reshape(c, h2, 2, w2, 2)
    return xr.max(axis=(2, 4)).astype(np.float32)


def linear(x: np.ndarray, w: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Linear: x @ w.T + b. w: (out, in), b: (out,). x: (in,) or (B, in)."""
    x = np.asarray(x, dtype=np.float32)
    w = np.asarray(w, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    return (x @ w.T + b).astype(np.float32)


def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    x = x - np.max(x, axis=axis, keepdims=True)
    e = np.exp(x).astype(np.float32)
    return (e / np.sum(e, axis=axis, keepdims=True)).astype(np.float32)


def sigmoid(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return (1.0 / (1.0 + np.exp(-x))).astype(np.float32)
