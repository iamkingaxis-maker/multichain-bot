"""Pure-numpy ChartCNN forward pass — no torch.

Loads weights from models/chart_cnn_v1.npz (exported from the torch .pt by
scripts/export_chart_weights.py) and reproduces ChartCNN's forward:
4x [conv3x3 -> bn -> relu -> maxpool2] over 3x64x64 -> flatten(1024) ->
fc(1024,128) -> relu -> dropout(identity at inference) ->
pattern_head(128,15) + outcome_head(128,1).

This is the SHADOW model (not live-gating). PATTERN_CLASSES / IDX_TO_CLASS
are duplicated here so the runtime never imports models.chart_cnn (which
pulls torch at module top).
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Tuple

import numpy as np

from core import np_nn

# Mirror of models.chart_cnn.PATTERN_CLASSES (kept in sync; do not reorder).
PATTERN_CLASSES = [
    "default",
    "1s_capit_reversal",
    "sweep_rejection",
    "extreme_sweep_1m",
    "demand_bottom_compound",
    "patient_bottom",
    "clean_break",
    "informed_cluster",
    "whale_conviction",
    "alpha_buyperscold",
    "grad_window_dip",
    "net_flow_5m_demand",
    "controlled_greens_5m",
    "pullback_in_uptrend",
    "other",
]
NUM_PATTERN_CLASSES = len(PATTERN_CLASSES)
CLASS_TO_IDX = {name: i for i, name in enumerate(PATTERN_CLASSES)}
IDX_TO_CLASS = {i: name for i, name in enumerate(PATTERN_CLASSES)}

_DEFAULT_NPZ = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "chart_cnn_v1.npz",
)


class ChartCNNNP:
    """Numpy port of models.chart_cnn.ChartCNN (inference only)."""

    def __init__(self, weights: Dict[str, np.ndarray]):
        self.w = weights

    @classmethod
    def from_npz(cls, npz_path: str = _DEFAULT_NPZ) -> "ChartCNNNP":
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

    def forward(self, img: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """img: (3, 64, 64) raw uint8 array.

        Returns (pattern_logits (15,), outcome_logit (1,)) float32.
        Dropout(0.3) is identity at inference (eval mode), matching torch.
        """
        x = np.asarray(img, dtype=np.float32) / np.float32(255.0)
        x = self._block(x, "block1")
        x = self._block(x, "block2")
        x = self._block(x, "block3")
        x = self._block(x, "block4")
        x = x.reshape(-1).astype(np.float32)  # flatten 1024
        x = np_nn.linear(x, self.w["fc.weight"], self.w["fc.bias"])
        x = np_nn.relu(x)
        # dropout = identity at inference
        pattern_logits = np_nn.linear(x, self.w["pattern_head.weight"], self.w["pattern_head.bias"])
        outcome_logit = np_nn.linear(x, self.w["outcome_head.weight"], self.w["outcome_head.bias"])
        return pattern_logits.astype(np.float32), outcome_logit.astype(np.float32)

    __call__ = forward


_singleton: Optional[ChartCNNNP] = None
