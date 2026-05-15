"""Late-fusion meta-model: CNN chart embedding + on-chain features -> P(win).

Trains a gradient boosting classifier (or fallback to logistic regression)
on the concatenation of:
  - 64-dim chart embedding from ChartEncoder
  - ~35 numeric features from entry_meta (whale signals, bs_h1/h6/m5,
    mtf_score, lp_locked_pct, top10_holder_pct, hour_ct, etc.)
  - 15-dim trigger one-hot (which triggers fired)

Output: probability of win (pnl > 0). Stored alongside cnn_outcome_prob
in entry_meta on every signal — eventually replaces it as the primary
quality score.

Training: pulls trade records from /api/trades with full entry_meta,
joins each buy with its CNN embedding (computed from the .npy at scan
time, or re-rendered from candles), trains the meta-model with
5-fold CV. Requires >= 200 closed trades — emits warning and exits if
fewer.

Output:
  - models/fusion_meta_v1.pkl (sklearn pipeline)
  - Per-fold WR/AUC stats

Usage:
    python scripts/train_fusion_meta_model.py
    python scripts/train_fusion_meta_model.py --min-trades 50  # for testing
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import torch

# ---------------------------------------------------------------------------
# sklearn availability guard — auto-install if missing
# ---------------------------------------------------------------------------
try:
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold
    from sklearn.preprocessing import StandardScaler
except ImportError:
    import subprocess
    print("sklearn not found — installing scikit-learn...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "scikit-learn"])
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold
    from sklearn.preprocessing import StandardScaler

from models.chart_autoencoder import ChartEncoder
from models.chart_cnn import CLASS_TO_IDX, PATTERN_CLASSES
from models.fusion_meta import ScaledLR

# ---------------------------------------------------------------------------
# Feature schema
# ---------------------------------------------------------------------------

# Numeric on-chain features extracted from entry_meta.
# Keys that are absent for a given trade will default to 0.0.
# Note:
#   - hour_ct is derived from the trade timestamp (not stored in entry_meta)
#   - entry_market_cap_usd is on the top-level trade record
#   - dev_holder_pct is absent; dev_balance_change_pct is used instead
NUMERIC_KEYS = [
    # buy/sell flow
    "bs_h1",
    "bs_h6",
    "bs_m5",
    # multi-timeframe alignment
    "chart_mtf_score",
    "chart_score",
    "chart_full_coverage",
    # holder concentration / safety
    "lp_locked_pct",
    "top1_holder_pct",
    "top10_holder_pct",
    "dev_balance_change_pct",  # proxy for dev_holder_pct (available in live data)
    "lp_imbalance_ratio",
    "rugcheck_score",
    # smart money
    "smart_wallet_count_60s",
    "smart_wallet_count_total",
    "smart_wallet_volume_usd",
    "smart_wallet_volume_pct",
    # trade velocity
    "buys_per_min_recent",
    "buy_pressure_60s",
    "unique_buyer_ratio",
    "median_buy_size_usd",
    "n_recurring_buyers_3plus",
    "whale_buy_present_2k",
    # 1m signals
    "1m_consec_red",
    "1m_volume_spike",
    "1m_last_close_pct",
    "1m_cum_3min_pct",
    # 5m / 1h range position
    "pct_in_5m_range",
    "pct_in_1h_range",
    # lifecycle
    "lifecycle_age_hours",
    "lifecycle_peak_h24_pct",
    # regime / macro
    "sol_pc_h1",
    "btc_pc_h1",
    "meme_sector_pct_h24",
    # time-of-day (derived from trade timestamp, CT hour 0-23)
    "__hour_ct",
    # market cap (from top-level trade record, not entry_meta)
    "__entry_market_cap_usd",
]

DATASET_DIR = Path(".cnn_dataset/v1")
API_URL = "https://gracious-inspiration-production.up.railway.app/api/trades?limit=2000"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def fetch_trades_with_outcomes() -> list[dict]:
    """Returns list of {address, time, entry_meta, pnl_pct, pnl_usd} for each
    buy that has at least one matching sell (closed trades only)."""
    r = urllib.request.urlopen(API_URL, timeout=30)
    trades = json.loads(r.read())
    if isinstance(trades, dict):
        trades = trades.get("trades", [])

    sells_by_addr: dict[str, list] = defaultdict(list)
    for t in trades:
        if t.get("type") == "sell":
            sells_by_addr[t.get("address", "")].append(t)

    paired = []
    for t in trades:
        if t.get("type") != "buy":
            continue
        addr = t.get("address", "")
        ts = t.get("time", "")
        # Match sells that occurred after this buy
        matching_sells = [
            s for s in sells_by_addr.get(addr, []) if s.get("time", "") > ts
        ]
        if not matching_sells:
            continue  # still open or no sell recorded

        pnl_usd = sum((s.get("pnl") or 0.0) for s in matching_sells)
        em = t.get("entry_meta") or {}
        amount = float(em.get("amount_usd") or 20.0)
        pnl_pct = (pnl_usd / max(amount, 1.0)) * 100.0

        paired.append(
            {
                "address": addr,
                "time": ts,
                "entry_meta": em,
                "entry_market_cap_usd": t.get("entry_market_cap_usd") or 0.0,
                "pnl_pct": pnl_pct,
                "pnl_usd": pnl_usd,
            }
        )

    return paired


def _load_chart_npy_for_trade(trade: dict) -> np.ndarray | None:
    """Resolve the .npy file for a trade using address + timestamp.

    The scanner saves files as:
        .cnn_dataset/v1/{address}_{timestamp_with_colons_replaced_by_dashes}.npy

    The API returns ISO 8601 timestamps like '2026-05-12T16:00:30.985957+00:00'.
    Replacing all ':' with '-' produces the filename component.
    """
    addr = trade.get("address", "")
    ts = trade.get("time", "")
    if not addr or not ts:
        return None
    safe_ts = ts.replace(":", "-")
    npy_path = DATASET_DIR / f"{addr}_{safe_ts}.npy"
    if not npy_path.exists():
        return None
    arr = np.load(npy_path)  # expected shape: (3, 64, 64) uint8
    return arr


def _derive_hour_ct(trade: dict) -> float:
    """Extract hour of day (0-23) in Central Time from ISO 8601 timestamp."""
    ts = trade.get("time", "")
    if not ts:
        return 0.0
    try:
        dt = datetime.fromisoformat(ts)
        # CT = UTC-5 (CST) or UTC-6 (CDT). Use fixed UTC-5 as approximation.
        ct_hour = (dt.hour - 5) % 24
        return float(ct_hour)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def extract_features(
    trade: dict, encoder: ChartEncoder
) -> tuple[np.ndarray, int] | None:
    """Returns (feature_vector, win_label) or None if CNN embedding unavailable.

    Feature vector layout:
        [64-dim CNN embedding] + [35 numeric on-chain] + [15-dim trigger one-hot]
        = 114 dimensions total
    """
    em = trade.get("entry_meta") or {}

    # --- 1. Numeric on-chain features ---
    numeric_vals: list[float] = []
    for k in NUMERIC_KEYS:
        if k == "__hour_ct":
            numeric_vals.append(_derive_hour_ct(trade))
        elif k == "__entry_market_cap_usd":
            numeric_vals.append(float(trade.get("entry_market_cap_usd") or 0.0))
        else:
            v = em.get(k)
            try:
                numeric_vals.append(float(v) if v is not None else 0.0)
            except (TypeError, ValueError):
                numeric_vals.append(0.0)

    numeric = np.array(numeric_vals, dtype=np.float32)

    # --- 2. Trigger one-hot (15 classes = PATTERN_CLASSES from chart_cnn) ---
    triggers_fired = em.get("triggers_fired") or []
    trigger_onehot = np.zeros(len(PATTERN_CLASSES), dtype=np.float32)
    for tr in triggers_fired:
        if tr in CLASS_TO_IDX:
            trigger_onehot[CLASS_TO_IDX[tr]] = 1.0

    # --- 3. CNN embedding ---
    img = _load_chart_npy_for_trade(trade)
    if img is None:
        return None  # can't compute embedding — skip this sample

    img_tensor = torch.from_numpy(img.astype(np.float32) / 255.0)
    if img_tensor.ndim == 3:
        img_tensor = img_tensor.unsqueeze(0)  # (1, 3, 64, 64)
    with torch.no_grad():
        embedding = encoder(img_tensor).cpu().numpy()[0]  # (64,)

    # --- 4. Label ---
    win_label = 1 if trade.get("pnl_pct", 0.0) > 0.0 else 0

    # Concatenate: [64 CNN] + [35 numeric] + [15 trigger one-hot] = 114 dims
    feature_vec = np.concatenate([embedding, numeric, trigger_onehot])
    return feature_vec, win_label


def _feature_names() -> list[str]:
    """Human-readable names for every dimension of the feature vector."""
    embed_names = [f"embed_{i}" for i in range(64)]
    numeric_names = [k.lstrip("_") for k in NUMERIC_KEYS]  # strip __ prefix
    trigger_names = [f"trigger_{c}" for c in PATTERN_CLASSES]
    return embed_names + numeric_names + trigger_names


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def train(
    features: np.ndarray,
    labels: np.ndarray,
    min_trades: int = 200,
) -> dict | None:
    """Train the fusion meta-model. Returns dict with model + CV stats, or None
    when there are too few samples."""
    n = len(labels)
    if n < min_trades:
        print(f"\nOnly {n} usable samples — minimum is {min_trades}.")
        print("Scaffolding is ready. Re-run when trade volume increases.")
        return None

    n_pos = int(labels.sum())
    n_neg = n - n_pos
    print(f"Class balance: {n_pos} wins / {n_neg} losses ({n_pos/n*100:.1f}% WR)")

    # Gradient boosting is the primary; fall back to logistic regression when
    # the dataset is still very small (< 80 samples) to avoid overfitting noise.
    use_gb = n >= 80
    if not use_gb:
        print("Dataset < 80 samples — using LogisticRegression fallback.")

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    fold_aucs: list[float] = []
    fold_accs: list[float] = []

    print("\n5-fold stratified CV:")
    for i, (train_idx, val_idx) in enumerate(cv.split(features, labels)):
        X_tr, X_val = features[train_idx], features[val_idx]
        y_tr, y_val = labels[train_idx], labels[val_idx]

        if use_gb:
            clf = GradientBoostingClassifier(
                n_estimators=100,
                max_depth=3,
                learning_rate=0.1,
                subsample=0.8,
                random_state=42,
            )
            clf.fit(X_tr, y_tr)
        else:
            scaler = StandardScaler()
            X_tr_sc = scaler.fit_transform(X_tr)
            X_val_sc = scaler.transform(X_val)
            clf = LogisticRegression(max_iter=1000, random_state=42, C=0.1)
            clf.fit(X_tr_sc, y_tr)
            # Wrap for consistent interface below
            clf = ScaledLR(scaler, clf)

        preds_proba = clf.predict_proba(X_val)[:, 1]
        preds_binary = (preds_proba >= 0.5).astype(int)

        # roc_auc_score requires at least one sample from each class
        if len(set(y_val)) < 2:
            auc = float("nan")
        else:
            auc = roc_auc_score(y_val, preds_proba)
        acc = accuracy_score(y_val, preds_binary)
        fold_aucs.append(auc)
        fold_accs.append(acc)
        print(f"  fold {i+1}: AUC={auc:.3f}  acc={acc:.3f}  n={len(y_val)}")

    # Final model trained on the full dataset
    print("\nTraining final model on full dataset...")
    if use_gb:
        final_clf = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=3,
            learning_rate=0.1,
            subsample=0.8,
            random_state=42,
        )
        final_clf.fit(features, labels)
        importances = list(zip(range(features.shape[1]), final_clf.feature_importances_))
    else:
        scaler = StandardScaler()
        X_sc = scaler.fit_transform(features)
        inner_clf = LogisticRegression(max_iter=1000, random_state=42, C=0.1)
        inner_clf.fit(X_sc, labels)
        final_clf = ScaledLR(scaler, inner_clf)
        coef_abs = np.abs(inner_clf.coef_[0])
        importances = list(zip(range(features.shape[1]), coef_abs))

    importances_sorted = sorted(importances, key=lambda x: -x[1])

    valid_aucs = [a for a in fold_aucs if not (isinstance(a, float) and a != a)]
    mean_auc = float(np.mean(valid_aucs)) if valid_aucs else float("nan")
    mean_acc = float(np.mean(fold_accs))

    return {
        "model": final_clf,
        "mean_auc": mean_auc,
        "mean_acc": mean_acc,
        "top_features": importances_sorted[:20],
        "n_samples": n,
        "use_gb": use_gb,
    }


# ScaledLR is imported from models.fusion_meta so that pickle deserialization
# works in any script that loads the .pkl without needing to re-import this file.


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_model(result: dict, out_path: Path) -> None:
    """Pickle the trained model + metadata for production inference."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": result["model"],
        "mean_auc": result["mean_auc"],
        "mean_acc": result["mean_acc"],
        "n_samples": result["n_samples"],
        "use_gb": result["use_gb"],
        "feature_names": _feature_names(),
        "numeric_keys": NUMERIC_KEYS,
        "pattern_classes": PATTERN_CLASSES,
        "embed_dim": 64,
        "feature_dim": 64 + len(NUMERIC_KEYS) + len(PATTERN_CLASSES),
    }
    with open(out_path, "wb") as f:
        pickle.dump(payload, f)
    print(f"Saved fusion meta-model to {out_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Train late-fusion meta-model (CNN embed + on-chain features)."
    )
    ap.add_argument(
        "--min-trades",
        type=int,
        default=200,
        help="Minimum closed trades required to train (default: 200). "
        "Use --min-trades 30 for a sanity-check run with current data.",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("models/fusion_meta_v1.pkl"),
        help="Output path for trained model pickle.",
    )
    args = ap.parse_args()

    # --- Load encoder ---
    encoder_path = Path("models/chart_encoder_v1.pt")
    if not encoder_path.exists():
        print(f"ERROR: encoder not found at {encoder_path}")
        print("Run `python scripts/train_chart_autoencoder.py` first.")
        sys.exit(1)

    print(f"Loading encoder from {encoder_path}...")
    encoder = ChartEncoder()
    encoder.load_state_dict(
        torch.load(encoder_path, map_location="cpu", weights_only=True)
    )
    encoder.eval()
    print("Encoder loaded.")

    # --- Fetch trades ---
    print(f"\nFetching trades from {API_URL}...")
    trades = fetch_trades_with_outcomes()
    print(f"Closed paired trades: {len(trades)}")

    if len(trades) == 0:
        print("No closed trades found. Cannot train.")
        sys.exit(0)

    # Early exit before feature extraction when obviously too few
    if len(trades) < args.min_trades:
        print(
            f"\nOnly {len(trades)} closed trades, need {args.min_trades}. "
            "Scaffolding is ready. Re-run when trade volume increases."
        )
        sys.exit(0)

    # --- Extract features ---
    print("\nExtracting features (requires matching .npy chart file per trade)...")
    features: list[np.ndarray] = []
    labels: list[int] = []
    skipped_no_npy = 0

    for t in trades:
        result = extract_features(t, encoder)
        if result is None:
            skipped_no_npy += 1
            continue
        feat, label = result
        features.append(feat)
        labels.append(label)

    print(
        f"Extracted: {len(features)} samples  "
        f"(skipped {skipped_no_npy} without .npy file)"
    )

    if len(features) == 0:
        print("No usable samples after feature extraction. Cannot train.")
        sys.exit(0)

    features_arr = np.array(features, dtype=np.float32)
    labels_arr = np.array(labels, dtype=np.int32)

    print(f"Feature matrix shape: {features_arr.shape}")
    print(
        f"Class balance: wins={labels_arr.sum()}/{len(labels_arr)} "
        f"({labels_arr.sum()/len(labels_arr)*100:.1f}% WR)"
    )

    # Post-extraction threshold check
    if len(features_arr) < args.min_trades:
        print(
            f"\nAfter feature extraction: only {len(features_arr)} usable samples "
            f"(need {args.min_trades})."
        )
        print("Scaffolding is ready. Re-run when trade volume increases.")
        sys.exit(0)

    # --- Train ---
    result = train(features_arr, labels_arr, min_trades=args.min_trades)
    if result is None:
        sys.exit(0)

    # --- Report ---
    print(f"\nMean CV AUC : {result['mean_auc']:.3f}")
    print(f"Mean CV Acc : {result['mean_acc']:.3f}")

    names = _feature_names()
    print(f"\nTop-20 features by importance:")
    for idx, imp in result["top_features"]:
        name = names[idx] if idx < len(names) else f"dim_{idx}"
        print(f"  {name:<40} {imp:.4f}")

    # --- Save ---
    save_model(result, args.out)


if __name__ == "__main__":
    main()
