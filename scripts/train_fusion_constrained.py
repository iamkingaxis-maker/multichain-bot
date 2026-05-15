"""Constrained fusion meta-model: 14 hand-picked features from real entry_meta.

Pulls closed trades from /api/trades (each carries the full entry_meta dict that
the scanner saved at buy-time — chart features, on-chain holder concentration,
CNN cluster_id, MTF score, triggers fired, etc.). Builds a 14-dim feature
vector per trade and trains an L2-regularized logistic regression with
leave-one-out cross-validation.

Why 14 features (and not the 114-feature scaffolding in
train_fusion_meta_model.py): n=55-60 closed trades is too small for 114
parameters — the original scaffolding emitted AUC ≈ 0.48 (noise). The 14
features here are hand-picked to span every signal category we have evidence
for (on-chain rug indicators, chart MTF, CNN cluster, 1m price action, regime)
while keeping k/n ≈ 0.24 — within the rule-of-thumb tolerance for LR + LOO-CV
+ strong L2.

Output:
  - models/fusion_constrained_v1.pkl (pickled ScaledLR + feature metadata)
  - LOO-CV AUC, accuracy, and top coefficients

Usage:
    python scripts/train_fusion_constrained.py
    python scripts/train_fusion_constrained.py --api-url <url> --out <path>
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.model_selection import LeaveOneOut
    from sklearn.preprocessing import StandardScaler
except ImportError:
    import subprocess
    print("sklearn not found — installing scikit-learn...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "scikit-learn"])
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.model_selection import LeaveOneOut
    from sklearn.preprocessing import StandardScaler

from models.fusion_meta import ScaledLR

API_URL_DEFAULT = "https://gracious-inspiration-production.up.railway.app/api/trades?limit=2000"

# Cluster IDs identified by the autoencoder + k-means analysis (see
# scripts/rug_predictor_analysis.py and scripts/analyze_chart_clusters.py).
# Cluster 19 = rug shape (67% rug rate, -18.5% avg pnl, n=6).
# Cluster 18 = winner shape (+bs_m5 ≥ 2.0 combo hit 100% WR, n=3).
RUG_CLUSTER_IDS = {19}
WINNER_CLUSTER_IDS = {18}

# Feature schema — order is load-bearing. Names must match the dict produced
# by extract_features() and the inference helper in models/fusion_constrained.py.
FEATURE_NAMES = [
    # On-chain (5)
    "bs_h1",
    "bs_m5",
    "top10_holder_pct",
    "lp_locked_pct",
    "rugcheck_score",
    # Chart / CNN (3)
    "chart_mtf_score",
    "cnn_cluster_is_rug",
    "cnn_cluster_is_winner",
    # 1m price action (4)
    "1m_cum_3min_pct",
    "1m_volume_spike",
    "pct_in_5m_range",
    "pc_h1_change_since_lookback",
    # Regime (2)
    "lifecycle_age_hours",
    "hour_ct",
]


def fetch_trades(api_url: str) -> list[dict]:
    r = urllib.request.urlopen(api_url, timeout=30)
    data = json.loads(r.read())
    return data if isinstance(data, list) else data.get("trades", [])


def pair_buys_with_pnl(trades: list[dict]) -> list[dict]:
    sells_by_addr: dict[str, list] = defaultdict(list)
    for t in trades:
        if t.get("type") == "sell":
            sells_by_addr[t.get("address", "")].append(t)
    paired: list[dict] = []
    for t in trades:
        if t.get("type") != "buy":
            continue
        addr = t.get("address", "")
        ts = t.get("time", "")
        matching = [s for s in sells_by_addr.get(addr, []) if s.get("time", "") > ts]
        if not matching:
            continue
        pnl_usd = sum((s.get("pnl") or 0.0) for s in matching)
        em = t.get("entry_meta") or {}
        amt = float(em.get("amount_usd") or 20.0)
        pnl_pct = (pnl_usd / max(amt, 1.0)) * 100.0
        paired.append({
            "address": addr,
            "time": ts,
            "entry_meta": em,
            "pnl_pct": pnl_pct,
            "pnl_usd": pnl_usd,
        })
    return paired


def _derive_hour_ct(time_iso: str) -> float:
    if not time_iso:
        return 0.0
    try:
        dt = datetime.fromisoformat(time_iso)
        return float((dt.hour - 5) % 24)  # UTC-5 approximation for CT
    except Exception:
        return 0.0


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def extract_features(entry_meta: dict, time_iso: str = "") -> np.ndarray:
    """Build the 14-dim feature vector from an entry_meta dict.

    This is the SAME function the inference helper uses — single source of truth
    for the feature schema.
    """
    em = entry_meta or {}
    cluster_id = em.get("cnn_cluster_id")
    cluster_is_rug = 1.0 if cluster_id is not None and cluster_id in RUG_CLUSTER_IDS else 0.0
    cluster_is_winner = 1.0 if cluster_id is not None and cluster_id in WINNER_CLUSTER_IDS else 0.0

    vec = np.array([
        _safe_float(em.get("bs_h1")),
        _safe_float(em.get("bs_m5")),
        _safe_float(em.get("top10_holder_pct")),
        _safe_float(em.get("lp_locked_pct")),
        _safe_float(em.get("rugcheck_score")),
        _safe_float(em.get("chart_mtf_score")),
        cluster_is_rug,
        cluster_is_winner,
        _safe_float(em.get("1m_cum_3min_pct")),
        _safe_float(em.get("1m_volume_spike")),
        _safe_float(em.get("pct_in_5m_range")),
        _safe_float(em.get("pc_h1_change_since_lookback")),
        _safe_float(em.get("lifecycle_age_hours")),
        _derive_hour_ct(time_iso),
    ], dtype=np.float32)
    return vec


def train_loo(X: np.ndarray, y: np.ndarray, C: float = 0.1) -> dict:
    """Leave-one-out CV + final model on full data."""
    n = len(y)
    loo = LeaveOneOut()
    val_probs: list[float] = []
    val_truths: list[int] = []
    for train_idx, val_idx in loo.split(X):
        X_tr, X_val = X[train_idx], X[val_idx]
        y_tr, y_val = y[train_idx], y[val_idx]
        scaler = StandardScaler()
        X_tr_sc = scaler.fit_transform(X_tr)
        X_val_sc = scaler.transform(X_val)
        clf = LogisticRegression(
            max_iter=2000, random_state=42, C=C, solver="liblinear", penalty="l2"
        )
        clf.fit(X_tr_sc, y_tr)
        val_probs.append(float(clf.predict_proba(X_val_sc)[0, 1]))
        val_truths.append(int(y_val[0]))

    val_probs_arr = np.array(val_probs)
    val_truths_arr = np.array(val_truths)
    if len(set(val_truths_arr.tolist())) < 2:
        auc = float("nan")
    else:
        auc = roc_auc_score(val_truths_arr, val_probs_arr)
    preds_binary = (val_probs_arr >= 0.5).astype(int)
    acc = accuracy_score(val_truths_arr, preds_binary)

    # Final model on full data
    final_scaler = StandardScaler()
    X_full = final_scaler.fit_transform(X)
    final_clf = LogisticRegression(
        max_iter=2000, random_state=42, C=C, solver="liblinear", penalty="l2"
    )
    final_clf.fit(X_full, y)

    return {
        "model": ScaledLR(final_scaler, final_clf),
        "loo_auc": float(auc),
        "loo_acc": float(acc),
        "coefficients": dict(zip(FEATURE_NAMES, final_clf.coef_[0].tolist())),
        "intercept": float(final_clf.intercept_[0]),
        "n_samples": n,
        "n_pos": int(y.sum()),
        "C": C,
        "val_probs": val_probs_arr.tolist(),
        "val_truths": val_truths_arr.tolist(),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api-url", default=API_URL_DEFAULT)
    ap.add_argument("--out", type=Path, default=Path("models/fusion_constrained_v1.pkl"))
    ap.add_argument("--min-trades", type=int, default=30,
                    help="Minimum closed trades required to train")
    ap.add_argument("--C", type=float, default=0.1,
                    help="Inverse L2 regularization strength (smaller = more regularization)")
    args = ap.parse_args()

    print(f"Fetching trades from {args.api_url}...")
    trades = fetch_trades(args.api_url)
    paired = pair_buys_with_pnl(trades)
    print(f"Closed paired trades: {len(paired)}")

    if len(paired) < args.min_trades:
        print(f"Need >= {args.min_trades} closed trades, have {len(paired)}. Exiting.")
        sys.exit(0)

    # Build features and labels
    X = np.stack([extract_features(p["entry_meta"], p["time"]) for p in paired])
    y = np.array([1 if p["pnl_pct"] > 0 else 0 for p in paired], dtype=np.int32)
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    print(f"Class balance: {n_pos} wins / {n_neg} losses ({n_pos/len(y)*100:.1f}% WR)")
    print(f"Feature matrix: {X.shape}")

    # Coverage check: how many features are populated per trade
    coverage_pct = (X != 0).mean(axis=0) * 100.0
    print("\nFeature coverage (% of trades with non-zero value):")
    for name, pct in zip(FEATURE_NAMES, coverage_pct):
        print(f"  {name:<32} {pct:5.1f}%")

    print(f"\nTraining LR (C={args.C}) with LOO-CV on n={len(y)}...")
    result = train_loo(X, y, C=args.C)
    print(f"\nLOO-CV: AUC={result['loo_auc']:.3f}  acc={result['loo_acc']:.3f}")

    print("\nFinal coefficients (sorted by |coef|):")
    coefs = sorted(result["coefficients"].items(), key=lambda kv: -abs(kv[1]))
    for name, c in coefs:
        sign = "+" if c >= 0 else "-"
        print(f"  {name:<32} {sign}{abs(c):.3f}")

    # Save
    payload = {
        "model": result["model"],
        "feature_names": FEATURE_NAMES,
        "rug_cluster_ids": list(RUG_CLUSTER_IDS),
        "winner_cluster_ids": list(WINNER_CLUSTER_IDS),
        "loo_auc": result["loo_auc"],
        "loo_acc": result["loo_acc"],
        "n_samples": result["n_samples"],
        "n_pos": result["n_pos"],
        "C": result["C"],
        "coefficients": result["coefficients"],
        "intercept": result["intercept"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "wb") as f:
        pickle.dump(payload, f)
    print(f"\nSaved fusion_constrained model to {args.out}")
    print(f"  n_samples={result['n_samples']}  AUC={result['loo_auc']:.3f}")


if __name__ == "__main__":
    main()
