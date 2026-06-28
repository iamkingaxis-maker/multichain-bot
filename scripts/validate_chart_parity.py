"""PARITY GATE (dev-only, uses torch): prove numpy chart models match torch.

The chart-CLUSTER encoder gates an ENFORCED live rug filter, so cluster-id
parity must be 100%. This script renders a large, diverse sample of chart
images (varied candle sequences) PLUS pure-random adversarial uint8 images,
runs BOTH the torch model and the numpy port on each, and reports:

  ENCODER/cluster: cluster_id (argmin over 20 centers) identical for 100%
                   of samples (REQUIRED). Max embedding abs-diff reported.
  CNN:             pattern argmax identical >= 99.5%; outcome_prob abs-diff
                   < 1e-3 (shadow, looser). Stats reported.

Exit 0 + prints PASS only if all gates met. Requires torch installed.

Usage:  python scripts/validate_chart_parity.py [--n 2500]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import torch  # noqa: E402

from feeds.candle_utils import Candle  # noqa: E402
from feeds.chart_image_renderer import render_chart_image  # noqa: E402
from models.chart_autoencoder import ChartEncoder  # noqa: E402
from models.chart_cnn import ChartCNN  # noqa: E402
from core.chart_encoder_np import ChartEncoderNP  # noqa: E402
from core.chart_cnn_np import ChartCNNNP  # noqa: E402

_MODELS = os.path.join(_ROOT, "models")
RNG = np.random.default_rng(20260628)


def _gen_candles(n: int, shape: str) -> list:
    """Generate a randomized candle sequence with a chosen macro-shape."""
    base_ts = 1700000000
    price = float(RNG.uniform(0.5, 5.0))
    candles = []
    for i in range(n):
        t = i / max(1, n - 1)
        if shape == "uptrend":
            drift = 0.01
        elif shape == "downtrend":
            drift = -0.01
        elif shape == "vshape":
            drift = -0.02 if t < 0.5 else 0.02
        elif shape == "spike":
            drift = 0.04 if 0.45 < t < 0.55 else -0.003
        elif shape == "flat":
            drift = 0.0
        else:  # random walk
            drift = 0.0
        vol = float(RNG.uniform(0.005, 0.05))
        ret = drift + RNG.normal(0, vol)
        new_price = max(1e-6, price * (1.0 + ret))
        o = price
        c = new_price
        hi = max(o, c) * (1.0 + abs(RNG.normal(0, vol)))
        lo = min(o, c) * (1.0 - abs(RNG.normal(0, vol)))
        lo = max(1e-7, lo)
        candles.append(Candle(open_time=base_ts + i * 60, open=o, high=hi,
                              low=lo, close=c, volume=float(RNG.uniform(10, 1000)),
                              close_time=base_ts + (i + 1) * 60))
        price = new_price
    return candles


_SHAPES = ["uptrend", "downtrend", "vshape", "spike", "flat", "rwalk"]


def _gen_rendered_image() -> np.ndarray | None:
    shape1 = _SHAPES[RNG.integers(len(_SHAPES))]
    shape5 = _SHAPES[RNG.integers(len(_SHAPES))]
    shape15 = _SHAPES[RNG.integers(len(_SHAPES))]
    n1 = int(RNG.integers(30, 90))
    n5 = int(RNG.integers(30, 90))
    n15 = int(RNG.integers(30, 90))
    img = render_chart_image(_gen_candles(n1, shape1),
                             _gen_candles(n5, shape5),
                             _gen_candles(n15, shape15))
    return img


def _build_sample(n: int):
    """Return list of (3,64,64) uint8 images: rendered + adversarial random."""
    imgs = []
    n_render = int(n * 0.7)
    n_random = n - n_render
    attempts = 0
    while len(imgs) < n_render and attempts < n_render * 5:
        attempts += 1
        img = _gen_rendered_image()
        if img is not None:
            imgs.append(img.astype(np.uint8))
    # Adversarial: pure random uint8 stress set.
    for _ in range(n_random):
        imgs.append(RNG.integers(0, 256, size=(3, 64, 64), dtype=np.uint8))
    return imgs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2500)
    args = ap.parse_args()

    print(f"Building parity sample (target {args.n}: ~70% rendered, ~30% adversarial random)...")
    imgs = _build_sample(args.n)
    print(f"  built {len(imgs)} images")

    # --- Load models ---
    centers = np.load(os.path.join(_MODELS, "chart_cluster_centers_v1.npy")).astype(np.float32)

    enc_t = ChartEncoder()
    enc_t.load_state_dict(torch.load(os.path.join(_MODELS, "chart_encoder_v1.pt"),
                                     map_location="cpu", weights_only=True))
    enc_t.eval()
    enc_np = ChartEncoderNP.from_npz(os.path.join(_MODELS, "chart_encoder_v1.npz"))

    cnn_t = ChartCNN()
    cnn_t.load_state_dict(torch.load(os.path.join(_MODELS, "chart_cnn_v1.pt"),
                                     map_location="cpu", weights_only=True))
    cnn_t.eval()
    cnn_np = ChartCNNNP.from_npz(os.path.join(_MODELS, "chart_cnn_v1.npz"))

    n = len(imgs)
    cluster_match = 0
    max_emb_diff = 0.0
    cnn_argmax_match = 0
    max_outcome_diff = 0.0
    max_pattern_logit_diff = 0.0
    mismatched_clusters = []

    with torch.no_grad():
        for i, img in enumerate(imgs):
            # ---- ENCODER / cluster ----
            t_in = torch.from_numpy(img).unsqueeze(0).float() / 255.0
            emb_t = enc_t(t_in).cpu().numpy()[0]
            emb_np = enc_np(img)
            max_emb_diff = max(max_emb_diff, float(np.max(np.abs(emb_t - emb_np))))
            cid_t = int(np.argmin(np.linalg.norm(centers - emb_t, axis=1)))
            cid_np = int(np.argmin(np.linalg.norm(centers - emb_np, axis=1)))
            if cid_t == cid_np:
                cluster_match += 1
            else:
                if len(mismatched_clusters) < 20:
                    mismatched_clusters.append((i, cid_t, cid_np))

            # ---- CNN ----
            pl_t, ol_t = cnn_t(torch.from_numpy(img).unsqueeze(0))
            pl_t = pl_t[0].cpu().numpy()
            outcome_t = float(torch.sigmoid(ol_t)[0, 0].item())
            pl_np, ol_np = cnn_np(img)
            outcome_np = float(1.0 / (1.0 + np.exp(-ol_np[0])))
            max_pattern_logit_diff = max(max_pattern_logit_diff,
                                         float(np.max(np.abs(pl_t - pl_np))))
            if int(np.argmax(pl_t)) == int(np.argmax(pl_np)):
                cnn_argmax_match += 1
            max_outcome_diff = max(max_outcome_diff, abs(outcome_t - outcome_np))

    cluster_pct = 100.0 * cluster_match / n
    cnn_pct = 100.0 * cnn_argmax_match / n

    print("\n========== PARITY RESULTS ==========")
    print(f"samples: {n}")
    print(f"[ENCODER] cluster-id match: {cluster_match}/{n} = {cluster_pct:.4f}%  (REQUIRE 100%)")
    print(f"[ENCODER] max embedding abs-diff: {max_emb_diff:.3e}")
    print(f"[CNN]     pattern argmax match: {cnn_argmax_match}/{n} = {cnn_pct:.4f}%  (REQUIRE >=99.5%)")
    print(f"[CNN]     max outcome_prob abs-diff: {max_outcome_diff:.3e}  (REQUIRE <1e-3)")
    print(f"[CNN]     max pattern logit abs-diff: {max_pattern_logit_diff:.3e}")
    if mismatched_clusters:
        print(f"cluster mismatches (idx, torch_cid, np_cid): {mismatched_clusters}")

    ok_cluster = cluster_match == n
    ok_cnn_argmax = cnn_pct >= 99.5
    ok_outcome = max_outcome_diff < 1e-3
    passed = ok_cluster and ok_cnn_argmax and ok_outcome

    print("------------------------------------")
    print(f"cluster 100%: {ok_cluster} | cnn argmax>=99.5%: {ok_cnn_argmax} | outcome<1e-3: {ok_outcome}")
    print("RESULT:", "PASS" if passed else "FAIL")
    print("====================================")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
