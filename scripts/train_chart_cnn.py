"""Train ChartCNN on the backfilled + forward-collected dataset.

Loads images + labels from .cnn_dataset/v1/, splits date-stratified
(train < cutoff, val >= cutoff), trains with combined cross-entropy +
BCE loss, saves best-val-loss model to models/chart_cnn_v1.pt.

Usage:
  python scripts/train_chart_cnn.py                 # use defaults
  python scripts/train_chart_cnn.py --epochs 20    # custom epochs
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from models.chart_cnn import ChartCNN, CLASS_TO_IDX, NUM_PATTERN_CLASSES

DATASET_DIRS = [
    Path(".cnn_dataset/v1"),         # actual trade backfill (55 samples, trigger-labeled)
    Path(".cnn_dataset/v2_broad"),   # broader-universe mined (1000+ samples)
]
MODEL_OUT = Path("models/chart_cnn_v1.pt")


class ChartDataset(Dataset):
    def __init__(self, items: list):
        self.items = items  # list of (image_path, label_dict)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx):
        img_path, label = self.items[idx]
        img = np.load(img_path)  # (3, 64, 64) uint8
        x = torch.from_numpy(img).float() / 255.0  # → (3, 64, 64) float
        pattern_idx = CLASS_TO_IDX.get(label.get("pattern_label") or "default", 0)
        outcome = float(label.get("outcome_label") or 0)
        return x, torch.tensor(pattern_idx, dtype=torch.long), torch.tensor(outcome, dtype=torch.float)


def load_dataset(cutoff_iso: str = "2026-05-13T00:00:00", val_token_pct: float = 0.20):
    """Returns (train_items, val_items), each a list of (path, label) tuples.

    Reads from BOTH .cnn_dataset/v1 (trade-backfilled) and
    .cnn_dataset/v2_broad/<token>/ (broader-universe mined).

    Splitting strategy: per-token random holdout. 20% of unique tokens
    go to val, the remaining 80% to train. Prevents same-token leakage
    where two adjacent time-windows from the same token end up split
    across train/val (which would let the model memorize the token
    rather than learn pattern shape).

    cutoff_iso is preserved for legacy callers but ignored when v2_broad
    data is present (which always dominates due to volume).
    """
    import hashlib
    train_items = []
    val_items = []
    npy_files = []
    for d in DATASET_DIRS:
        npy_files.extend(sorted(glob.glob(str(d / "*.npy"))))
        npy_files.extend(sorted(glob.glob(str(d / "**" / "*.npy"), recursive=True)))
    npy_files = sorted(set(npy_files))
    for npy_path in npy_files:
        json_path = npy_path.replace(".npy", ".json")
        if not os.path.exists(json_path):
            continue
        with open(json_path) as f:
            label = json.load(f)
        addr = (label.get("addr") or "").lower()
        # Deterministic hash → val bucket if hash < val_token_pct
        h = int(hashlib.md5(addr.encode()).hexdigest(), 16) / 2**128
        if h < val_token_pct:
            val_items.append((npy_path, label))
        else:
            train_items.append((npy_path, label))
    return train_items, val_items


def train_one_epoch(model, loader, opt, pat_loss_fn, out_loss_fn):
    model.train()
    total = 0.0
    n = 0
    for x, pat_y, out_y in loader:
        opt.zero_grad()
        pat_logits, out_logit = model(x)
        loss = pat_loss_fn(pat_logits, pat_y) + out_loss_fn(out_logit.squeeze(-1), out_y)
        loss.backward()
        opt.step()
        total += loss.item() * x.size(0)
        n += x.size(0)
    return total / max(1, n)


@torch.no_grad()
def eval_one_epoch(model, loader, pat_loss_fn, out_loss_fn):
    model.eval()
    total = 0.0
    n = 0
    pat_correct = 0
    for x, pat_y, out_y in loader:
        pat_logits, out_logit = model(x)
        loss = pat_loss_fn(pat_logits, pat_y) + out_loss_fn(out_logit.squeeze(-1), out_y)
        total += loss.item() * x.size(0)
        n += x.size(0)
        pat_correct += (pat_logits.argmax(dim=1) == pat_y).sum().item()
    avg_loss = total / max(1, n)
    pat_acc = pat_correct / max(1, n)
    return avg_loss, pat_acc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--cutoff", default="2026-05-13T00:00:00",
                    help="ISO timestamp; entries before = train, >= = val")
    args = ap.parse_args()

    train_items, val_items = load_dataset(args.cutoff)
    print(f"train={len(train_items)}  val={len(val_items)}")
    if len(train_items) < 10 or len(val_items) < 5:
        print("Dataset too small to train. Need >=10 train and >=5 val.")
        return

    train_ds = ChartDataset(train_items)
    val_ds = ChartDataset(val_items)
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False)

    model = ChartCNN()
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    pat_loss_fn = nn.CrossEntropyLoss()
    out_loss_fn = nn.BCEWithLogitsLoss()

    best_val = float("inf")
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    for ep in range(1, args.epochs + 1):
        train_loss = train_one_epoch(model, train_loader, opt, pat_loss_fn, out_loss_fn)
        val_loss, pat_acc = eval_one_epoch(model, val_loader, pat_loss_fn, out_loss_fn)
        marker = ""
        if val_loss < best_val:
            best_val = val_loss
            torch.save(model.state_dict(), MODEL_OUT)
            marker = "  (best saved)"
        print(f"ep={ep:02d}  train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  "
              f"val_pat_acc={pat_acc:.3f}{marker}")

    print(f"\nBest val loss: {best_val:.4f}")
    print(f"Saved to: {MODEL_OUT}")


if __name__ == "__main__":
    main()
