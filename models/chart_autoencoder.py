"""ChartAutoencoder — convolutional autoencoder for chart shape discovery.

Architecture mirrors ChartCNN's encoder backbone, plus a transposed-conv
decoder that reconstructs the original 3x64x64 image. Training is
unsupervised: minimize MSE between input and reconstruction. The encoder's
64-dim bottleneck embedding captures the visual essence of each chart.

After training, k-means or HDBSCAN over embeddings discovers clusters.
Each cluster is a memecoin-native pattern — labelled afterward by
cross-referencing with outcomes and any existing pattern hints.

Total params: ~200K (encoder + decoder). CPU inference ~30ms.
"""
from __future__ import annotations
import torch
import torch.nn as nn


EMBED_DIM = 64


class _EncBlock(nn.Module):
    """Conv -> BN -> ReLU -> MaxPool(2) — same as ChartCNN."""
    def __init__(self, in_ch: int, out_ch: int):
        super().__init__()
        self.conv = nn.Conv2d(in_ch, out_ch, kernel_size=3, padding=1)
        self.bn = nn.BatchNorm2d(out_ch)
        self.act = nn.ReLU(inplace=True)
        self.pool = nn.MaxPool2d(kernel_size=2)

    def forward(self, x):
        return self.pool(self.act(self.bn(self.conv(x))))


class _DecBlock(nn.Module):
    """Upsample (nearest) -> Conv -> BN -> ReLU. Mirror of _EncBlock.

    Uses upsampling + conv instead of ConvTranspose for cleaner
    reconstructions (no checkerboard artifacts).
    """
    def __init__(self, in_ch: int, out_ch: int):
        super().__init__()
        self.up = nn.Upsample(scale_factor=2, mode='nearest')
        self.conv = nn.Conv2d(in_ch, out_ch, kernel_size=3, padding=1)
        self.bn = nn.BatchNorm2d(out_ch)
        self.act = nn.ReLU(inplace=True)

    def forward(self, x):
        return self.act(self.bn(self.conv(self.up(x))))


class ChartEncoder(nn.Module):
    """64x64x3 -> 64-dim embedding."""
    def __init__(self):
        super().__init__()
        # 64x64 -> 32x32 -> 16x16 -> 8x8 -> 4x4
        self.block1 = _EncBlock(3, 16)
        self.block2 = _EncBlock(16, 32)
        self.block3 = _EncBlock(32, 48)
        self.block4 = _EncBlock(48, 64)
        self.flat = nn.Flatten()
        self.fc1 = nn.Linear(1024, 128)  # 64*4*4
        self.fc_act = nn.ReLU(inplace=True)
        self.fc_embed = nn.Linear(128, EMBED_DIM)

    def forward(self, x):
        if x.dtype == torch.uint8:
            x = x.float() / 255.0
        x = self.block1(x)
        x = self.block2(x)
        x = self.block3(x)
        x = self.block4(x)
        x = self.flat(x)
        x = self.fc_act(self.fc1(x))
        return self.fc_embed(x)


class ChartDecoder(nn.Module):
    """64-dim embedding -> 3x64x64 reconstruction."""
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(EMBED_DIM, 128)
        self.fc_act = nn.ReLU(inplace=True)
        self.fc2 = nn.Linear(128, 1024)
        # 4x4 -> 8x8 -> 16x16 -> 32x32 -> 64x64
        self.block1 = _DecBlock(64, 48)
        self.block2 = _DecBlock(48, 32)
        self.block3 = _DecBlock(32, 16)
        self.block4 = _DecBlock(16, 16)
        self.final = nn.Conv2d(16, 3, kernel_size=3, padding=1)
        # sigmoid to keep output in [0, 1] matching input range
        self.out_act = nn.Sigmoid()

    def forward(self, z):
        x = self.fc_act(self.fc1(z))
        x = self.fc2(x)
        x = x.view(-1, 64, 4, 4)
        x = self.block1(x)
        x = self.block2(x)
        x = self.block3(x)
        x = self.block4(x)
        x = self.final(x)
        return self.out_act(x)


class ChartAutoencoder(nn.Module):
    """Full encoder + decoder. Training: minimize MSE between input and reconstruct(encode(input))."""
    def __init__(self):
        super().__init__()
        self.encoder = ChartEncoder()
        self.decoder = ChartDecoder()

    def forward(self, x):
        z = self.encoder(x)
        return self.decoder(z), z

    def encode(self, x):
        return self.encoder(x)
