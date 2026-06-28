"""Pure-numpy ChartEncoder forward pass — no torch.

Loads weights from models/chart_encoder_v1.npz (exported from the torch
.pt by scripts/export_chart_weights.py) and reproduces ChartEncoder's
forward exactly: 4x [conv3x3 -> bn -> relu -> maxpool2] backbone over
3x64x64 -> flatten(1024) -> fc1(1024,128) -> relu -> fc_embed(128,64).

This is the ENFORCED live rug-filter encoder. Parity vs torch is proven
by scripts/validate_chart_parity.py (100% cluster-id match required).
"""
from __future__ import annotations

import os
from typing import Dict, Optional

import numpy as np

from core import np_nn

_DEFAULT_NPZ = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "chart_encoder_v1.npz",
)


class ChartEncoderNP:
    """Numpy port of models.chart_autoencoder.ChartEncoder (inference only)."""

    def __init__(self, weights: Dict[str, np.ndarray]):
        self.w = weights

    @classmethod
    def from_npz(cls, npz_path: str = _DEFAULT_NPZ) -> "ChartEncoderNP":
        data = np.load(npz_path)
        weights = {k: data[k].astype(np.float32) for k in data.files}
        return cls(weights)

    def _block(self, x: np.ndarray, prefix: str) -> np.ndarray:
        w = self.w
        x = np_nn.conv2d_3x3_pad1(x, w[f"{prefix}.conv.weight"], w[f"{prefix}.conv.bias"])
        x = np_nn.batchnorm2d_infer(
            x, w[f"{prefix}.bn.weight"], w[f"{prefix}.bn.bias"],
            w[f"{prefix}.bn.running_mean"], w[f"{prefix}.bn.running_var"], eps=1e-5,
        )
        x = np_nn.relu(x)
        x = np_nn.maxpool2d_k2(x)
        return x

    def forward(self, img: np.ndarray) -> np.ndarray:
        """img: (3, 64, 64) raw uint8 array. Returns (64,) float32 embedding.

        Normalizes x/255.0 internally, matching the torch inference path
        (tensor = from_numpy(img).float() / 255.0).
        """
        x = np.asarray(img, dtype=np.float32) / np.float32(255.0)
        x = self._block(x, "block1")
        x = self._block(x, "block2")
        x = self._block(x, "block3")
        x = self._block(x, "block4")
        x = x.reshape(-1).astype(np.float32)  # flatten 1024
        x = np_nn.linear(x, self.w["fc1.weight"], self.w["fc1.bias"])
        x = np_nn.relu(x)
        x = np_nn.linear(x, self.w["fc_embed.weight"], self.w["fc_embed.bias"])
        return x.astype(np.float32)

    __call__ = forward


_singleton: Optional[ChartEncoderNP] = None
