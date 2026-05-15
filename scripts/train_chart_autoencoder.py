"""Train ChartAutoencoder on the full chart dataset (no labels).

Per-token 80/20 split (same as supervised training). Loss: MSE between
input and reconstruction. The encoder's 64-dim embedding gets used by
cluster_chart_embeddings.py.

Usage:
    python scripts/train_chart_autoencoder.py
    python scripts/train_chart_autoencoder.py --epochs 30 --batch-size 32
"""
from __future__ import annotations
import argparse
import glob
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from models.chart_autoencoder import ChartAutoencoder

DATASET_DIRS = [Path(".cnn_dataset/v1"), Path(".cnn_dataset/v2_broad")]
MODEL_OUT = Path("models/chart_autoencoder_v1.pt")
ENCODER_OUT = Path("models/chart_encoder_v1.pt")


class ChartImageDataset(Dataset):
    """Unsupervised — just loads images, no labels."""
    def __init__(self, paths: list):
        self.paths = paths

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, idx):
        img = np.load(self.paths[idx])  # (3, 64, 64) uint8
        x = torch.from_numpy(img).float() / 255.0
        return x


def collect_paths(val_token_pct: float = 0.20):
    """Returns (train_paths, val_paths). Per-token split — extract addr from
    json filename pair to determine split bucket."""
    train_paths, val_paths = [], []
    npy_files = []
    for d in DATASET_DIRS:
        npy_files.extend(glob.glob(str(d / "*.npy")))
        npy_files.extend(glob.glob(str(d / "**" / "*.npy"), recursive=True))
    npy_files = sorted(set(npy_files))
    for npy_path in npy_files:
        json_path = npy_path.replace(".npy", ".json")
        if not os.path.exists(json_path):
            continue
        try:
            with open(json_path) as f:
                label = json.load(f)
            addr = (label.get("addr") or "").lower()
        except Exception:
            continue
        h = int(hashlib.md5(addr.encode()).hexdigest(), 16) / 2**128
        if h < val_token_pct:
            val_paths.append(npy_path)
        else:
            train_paths.append(npy_path)
    return train_paths, val_paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    args = ap.parse_args()

    train_paths, val_paths = collect_paths()
    print(f"train={len(train_paths)} val={len(val_paths)}")
    if len(train_paths) < 50:
        print("Too few train samples.")
        return

    train_ds = ChartImageDataset(train_paths)
    val_ds = ChartImageDataset(val_paths)
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False)

    model = ChartAutoencoder()
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = nn.MSELoss()

    best_val = float("inf")
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    for ep in range(1, args.epochs + 1):
        model.train()
        train_loss_sum, train_n = 0.0, 0
        for x in train_loader:
            opt.zero_grad()
            recon, _ = model(x)
            loss = loss_fn(recon, x)
            loss.backward()
            opt.step()
            train_loss_sum += loss.item() * x.size(0)
            train_n += x.size(0)
        train_loss = train_loss_sum / max(1, train_n)

        # Eval
        model.eval()
        val_loss_sum, val_n = 0.0, 0
        with torch.no_grad():
            for x in val_loader:
                recon, _ = model(x)
                loss = loss_fn(recon, x)
                val_loss_sum += loss.item() * x.size(0)
                val_n += x.size(0)
        val_loss = val_loss_sum / max(1, val_n)

        marker = ""
        if val_loss < best_val:
            best_val = val_loss
            torch.save(model.state_dict(), MODEL_OUT)
            torch.save(model.encoder.state_dict(), ENCODER_OUT)
            marker = "  (best saved)"
        print(f"ep={ep:02d}  train={train_loss:.4f}  val={val_loss:.4f}{marker}")

    print(f"\nBest val loss: {best_val:.4f}")
    print(f"Saved AE to {MODEL_OUT}")
    print(f"Saved encoder to {ENCODER_OUT}")


if __name__ == "__main__":
    main()
