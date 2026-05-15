"""Cluster chart embeddings — discover memecoin patterns unsupervised.

Loads the encoder trained by train_chart_autoencoder.py. Encodes every
.npy in the dataset to a 64-dim embedding. Runs k-means with N=20
clusters (configurable). Writes cluster assignments back into each
.json as `cluster_id` for downstream analysis.

Usage:
    python scripts/cluster_chart_embeddings.py
    python scripts/cluster_chart_embeddings.py --n-clusters 30
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

from models.chart_autoencoder import ChartEncoder, EMBED_DIM

DATASET_DIRS = [Path(".cnn_dataset/v1"), Path(".cnn_dataset/v2_broad")]
ENCODER_WEIGHTS = Path("models/chart_encoder_v1.pt")


def load_encoder():
    enc = ChartEncoder()
    enc.load_state_dict(torch.load(ENCODER_WEIGHTS, map_location="cpu", weights_only=True))
    enc.eval()
    return enc


def collect_samples():
    """Returns list of (npy_path, json_path) for every paired sample."""
    pairs = []
    for d in DATASET_DIRS:
        npy_files = glob.glob(str(d / "*.npy")) + glob.glob(str(d / "**" / "*.npy"), recursive=True)
        for n in npy_files:
            j = n.replace(".npy", ".json")
            if os.path.exists(j):
                pairs.append((n, j))
    return sorted(set(pairs))


def encode_all(encoder: ChartEncoder, pairs: list, batch_size: int = 32):
    """Returns np.ndarray of shape (N, EMBED_DIM)."""
    embeddings = []
    batch = []
    for npy_path, _ in pairs:
        img = np.load(npy_path)  # (3, 64, 64) uint8
        x = torch.from_numpy(img).float() / 255.0
        batch.append(x)
        if len(batch) >= batch_size:
            x_b = torch.stack(batch)
            with torch.no_grad():
                z = encoder(x_b).cpu().numpy()
            embeddings.append(z)
            batch = []
    if batch:
        x_b = torch.stack(batch)
        with torch.no_grad():
            z = encoder(x_b).cpu().numpy()
        embeddings.append(z)
    return np.concatenate(embeddings, axis=0)


def kmeans(X: np.ndarray, n_clusters: int, n_iter: int = 100):
    """Simple k-means implementation (no sklearn dependency)."""
    rng = np.random.RandomState(42)
    n, d = X.shape
    # k-means++ init
    centers = [X[rng.randint(n)]]
    for _ in range(n_clusters - 1):
        dists = np.min(np.linalg.norm(X[:, None] - np.array(centers)[None], axis=2) ** 2, axis=1)
        probs = dists / dists.sum()
        idx = rng.choice(n, p=probs)
        centers.append(X[idx])
    centers = np.array(centers)

    for it in range(n_iter):
        dists = np.linalg.norm(X[:, None] - centers[None], axis=2)
        labels = np.argmin(dists, axis=1)
        new_centers = np.array([
            X[labels == k].mean(axis=0) if (labels == k).any() else centers[k]
            for k in range(n_clusters)
        ])
        if np.allclose(new_centers, centers, atol=1e-6):
            break
        centers = new_centers
    return labels, centers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-clusters", type=int, default=20)
    args = ap.parse_args()

    if not ENCODER_WEIGHTS.exists():
        print(f"Encoder weights not found at {ENCODER_WEIGHTS}. Train first.")
        return

    encoder = load_encoder()
    pairs = collect_samples()
    print(f"Encoding {len(pairs)} samples...")
    X = encode_all(encoder, pairs)
    print(f"Embeddings shape: {X.shape}")

    print(f"Clustering into {args.n_clusters} clusters...")
    labels, centers = kmeans(X, args.n_clusters)
    print(f"Cluster sizes (top 10): {sorted(np.bincount(labels), reverse=True)[:10]}")

    # Write cluster_id into each JSON
    written = 0
    for (npy_path, json_path), cl in zip(pairs, labels):
        try:
            with open(json_path) as f:
                d = json.load(f)
            d["cluster_id"] = int(cl)
            with open(json_path, "w") as f:
                json.dump(d, f)
            written += 1
        except Exception:
            pass
    print(f"Wrote cluster_id into {written} JSONs.")

    # Save embeddings + centers for downstream analysis
    np.savez(".cnn_dataset/_embeddings.npz",
             paths=np.array([p for p, _ in pairs]),
             embeddings=X,
             labels=labels,
             centers=centers)
    print("Saved embeddings to .cnn_dataset/_embeddings.npz")


if __name__ == "__main__":
    main()
