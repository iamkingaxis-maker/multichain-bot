"""ChartCNN — 4-layer CNN with two heads:
  1. Pattern classifier (softmax over NUM_PATTERN_CLASSES)
  2. Outcome regressor (sigmoid, win probability)

Input: (B, 3, 64, 64) uint8 → cast to float32 / 255.0 internally.
Total params: ~100K. CPU-target inference: 20-50ms.
"""
from __future__ import annotations
import torch
import torch.nn as nn

# Trigger-based pattern classes (v2 label vocabulary, 2026-05-15).
# Each label is the name of one of our entry triggers — the model
# learns "what does the chart look like when each trigger fires."
# Multi-trigger buys map to the first trigger in PATTERN_CLASSES order
# (priority: most distinctive/rare wins).
PATTERN_CLASSES = [
    "default",                     # no trigger fired (forward-miner fallback)
    "1s_capit_reversal",           # V-shape capitulation rare/distinctive
    "sweep_rejection",
    "extreme_sweep_1m",            # sub-1m sweep
    "demand_bottom_compound",
    "patient_bottom",              # most common (23 buys)
    "clean_break",
    "informed_cluster",
    "whale_conviction",
    "alpha_buyperscold",
    "grad_window_dip",
    "net_flow_5m_demand",
    "controlled_greens_5m",
    "pullback_in_uptrend",
    "other",                       # rare triggers lumped (hh10_*, vol_surge_recent, etc.)
]
NUM_PATTERN_CLASSES = len(PATTERN_CLASSES)
CLASS_TO_IDX = {name: i for i, name in enumerate(PATTERN_CLASSES)}
IDX_TO_CLASS = {i: name for i, name in enumerate(PATTERN_CLASSES)}


class _ConvBlock(nn.Module):
    """Conv → BatchNorm → ReLU → MaxPool(2)."""

    def __init__(self, in_ch: int, out_ch: int):
        super().__init__()
        self.conv = nn.Conv2d(in_ch, out_ch, kernel_size=3, padding=1)
        self.bn = nn.BatchNorm2d(out_ch)
        self.act = nn.ReLU(inplace=True)
        self.pool = nn.MaxPool2d(kernel_size=2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.pool(self.act(self.bn(self.conv(x))))


class ChartCNN(nn.Module):
    """4-layer CNN with shared backbone + two heads."""

    def __init__(self):
        super().__init__()
        # 64x64 → 32x32 → 16x16 → 8x8 → 4x4
        self.block1 = _ConvBlock(3, 16)
        self.block2 = _ConvBlock(16, 32)
        self.block3 = _ConvBlock(32, 48)
        self.block4 = _ConvBlock(48, 64)
        self.flat = nn.Flatten()
        # 64 channels * 4 * 4 = 1024
        self.fc = nn.Linear(1024, 128)
        self.fc_act = nn.ReLU(inplace=True)
        self.dropout = nn.Dropout(0.3)
        self.pattern_head = nn.Linear(128, NUM_PATTERN_CLASSES)
        self.outcome_head = nn.Linear(128, 1)

    def forward(self, x: torch.Tensor):
        # Cast uint8 → float32 [0, 1] if necessary
        if x.dtype == torch.uint8:
            x = x.float() / 255.0
        x = self.block1(x)
        x = self.block2(x)
        x = self.block3(x)
        x = self.block4(x)
        x = self.flat(x)
        x = self.fc_act(self.fc(x))
        x = self.dropout(x)
        pattern_logits = self.pattern_head(x)
        outcome_logit = self.outcome_head(x)
        return pattern_logits, outcome_logit
