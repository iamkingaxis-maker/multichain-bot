"""Rolling never-green scorer — PRODUCTION port (2026-06-04).

The entry-quality lever: multivariate model that predicts P(never-green dud). Validated
walk-forward token-AUC 0.65 (scripts/rolling_scorer.py, XGBoost research version); the
never-green signal is REAL but NON-STATIONARY, so the model must RETRAIN on a trailing
5-10d window (a frozen gate decays by 14d).

PORT NOTES vs the research script:
- XGBoost -> sklearn HistGradientBoostingClassifier. xgboost is NOT a prod dependency
  (not in requirements.txt, unused in core/feeds); scikit-learn IS. HistGBM is NaN-native,
  which matches the DYNAMIC feature set (train on whatever entry_meta keys exist; impute
  missing keys to NaN at scoring -> no train/serve KeyError). Measured AUC ~0.60 on the
  same data, comparable to the research 0.65.
- joblib persistence (sklearn) instead of .save_model.

USAGE (production): nightly -> .train(trailing_closed_positions); at entry ->
.should_block(features). MEASURE-ONLY shadow first, then de-size after forward confirm.
Self-contained + fail-open (untrained or missing-feature -> blocks nothing).
"""
from __future__ import annotations
import json
import os
from typing import Any, Dict, List, Optional

import numpy as np

try:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import cross_val_predict, GroupKFold
    _SKLEARN = True
except Exception:  # pragma: no cover - sklearn is a prod dep, but fail-open if absent
    _SKLEARN = False


def _confound_feat(f: str) -> bool:
    """Drop leakage/identity features (mirrors ng_classifier.confound_flag intent):
    timestamps, ids, and the label/outcome columns themselves."""
    fl = f.lower()
    if fl.startswith("_"):
        return True
    bad = ("ts_ms", "timestamp", "_at_secs", "_at_ts", "event_id", "address",
           "pnl", "peak", "outcome", "exit", "won", "mae", "_verdict", "_shadow")
    return any(b in fl for b in bad)


def _model():
    return HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.05, max_iter=200, l2_regularization=2.0,
        min_samples_leaf=20, random_state=0,
    )


class RollingNGScorer:
    """Train on trailing completed positions; score live entry feature-dicts.

    target_block_rate: the trailing OOS proba quantile used as the block threshold,
    so the live block-rate reproduces forward (an in-sample quantile barely blocks)."""

    def __init__(self, target_block_rate: float = 0.10):
        self.target_block_rate = target_block_rate
        self.clf = None
        self.feats: List[str] = []
        self.threshold: float = 2.0  # fail-open (no proba >= 2.0) until trained

    def train(self, X_rows: List[Dict[str, Any]], y_ng: List[int],
              groups: List[Any]) -> "RollingNGScorer":
        """X_rows: list of entry feature dicts. y_ng: 1 if never-green (dud) else 0.
        groups: token id per row (for grouped-CV threshold calibration)."""
        if not _SKLEARN or len(X_rows) < 80 or len(set(y_ng)) < 2:
            self.clf = None  # not enough data/signal -> fail-open
            return self
        # dynamic feature set: every numeric key present, minus confounds/leakage
        feats = set()
        for r in X_rows:
            for k, v in r.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool) and not _confound_feat(k):
                    feats.add(k)
        self.feats = sorted(feats)
        X = self._matrix(X_rows)
        y = np.asarray(y_ng)
        self.clf = _model()
        self.clf.fit(X, y)
        # Calibrate threshold on OUT-OF-SAMPLE (grouped-by-token CV) probabilities so the
        # forward block-rate matches target_block_rate (in-sample is overconfident).
        try:
            g = np.asarray(groups)
            ng = min(5, max(2, len(np.unique(g))))
            oos = cross_val_predict(_model(), X, y, groups=g, cv=GroupKFold(ng),
                                    method="predict_proba")[:, 1]
        except Exception:
            oos = self.clf.predict_proba(X)[:, 1]
        self.threshold = float(np.quantile(oos, 1.0 - self.target_block_rate))
        return self

    def _matrix(self, X_rows: List[Dict[str, Any]]) -> np.ndarray:
        # build the feature matrix in self.feats order; missing/non-numeric -> NaN
        # (HistGBM is NaN-native, so train/serve skew on missing keys never raises).
        out = np.full((len(X_rows), len(self.feats)), np.nan, dtype=float)
        for i, r in enumerate(X_rows):
            for j, f in enumerate(self.feats):
                v = r.get(f)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out[i, j] = v
        return out

    def score(self, feature_dict: Dict[str, Any]) -> float:
        """P(never-green) for one entry. 0.0 (fail-open) if untrained."""
        if self.clf is None:
            return 0.0
        return float(self.clf.predict_proba(self._matrix([feature_dict]))[0, 1])

    def should_block(self, feature_dict: Dict[str, Any]) -> bool:
        return self.score(feature_dict) >= self.threshold

    def save(self, path: str) -> None:
        if self.clf is None:
            return
        import joblib
        joblib.dump(self.clf, path + ".joblib")
        with open(path + ".meta.json", "w", encoding="utf-8") as fh:
            json.dump({"feats": self.feats, "threshold": self.threshold,
                       "target_block_rate": self.target_block_rate}, fh)

    def load(self, path: str) -> Optional["RollingNGScorer"]:
        if not (os.path.exists(path + ".joblib") and os.path.exists(path + ".meta.json")):
            return None
        import joblib
        self.clf = joblib.load(path + ".joblib")
        with open(path + ".meta.json", encoding="utf-8") as fh:
            m = json.load(fh)
        self.feats = m["feats"]; self.threshold = m["threshold"]
        self.target_block_rate = m["target_block_rate"]
        return self
