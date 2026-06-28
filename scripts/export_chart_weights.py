"""DEV-ONLY: export torch chart-model weights to numpy .npz.

Requires torch installed locally. Loads the two .pt state_dicts and writes
every tensor as a named float32 numpy array into:
  - models/chart_encoder_v1.npz
  - models/chart_cnn_v1.npz

The live runtime loads these .npz files (no torch). Re-run this whenever
the .pt weights change, then re-run scripts/validate_chart_parity.py.

Usage:  python scripts/export_chart_weights.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import torch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODELS = os.path.join(_ROOT, "models")


def _export(pt_path: str, npz_path: str) -> None:
    sd = torch.load(pt_path, map_location="cpu", weights_only=True)
    arrays = {}
    for k, v in sd.items():
        if k.endswith("num_batches_tracked"):
            continue  # not needed for inference
        arrays[k] = v.detach().cpu().numpy().astype(np.float32)
    np.savez(npz_path, **arrays)
    print(f"  {os.path.basename(pt_path)} -> {os.path.basename(npz_path)} "
          f"({len(arrays)} tensors)")


def main() -> int:
    print("Exporting chart-model weights torch -> numpy .npz ...")
    _export(os.path.join(_MODELS, "chart_encoder_v1.pt"),
            os.path.join(_MODELS, "chart_encoder_v1.npz"))
    _export(os.path.join(_MODELS, "chart_cnn_v1.pt"),
            os.path.join(_MODELS, "chart_cnn_v1.npz"))
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
