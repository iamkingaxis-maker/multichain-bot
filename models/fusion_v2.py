"""Production inference singleton for fusion_v2.

12-feature regularized LR (C=0.1) with median imputation + standard scaling.
10-fold CV AUC mean=0.737 (std=0.240) on n=90 paired trades.

Loads models/fusion_v2.pkl, exposes score_from_entry_meta(em) returning
P(win) in [0, 1]. Fail-quiet when model unavailable.

Stamped by dip_scanner as `fusion_v2_score_shadow` on every entry —
SHADOW only, does not gate.
"""
from __future__ import annotations

import logging
import math
import os
import pickle
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "fusion_v2.pkl",
)


def _safe_float(v) -> float:
    try:
        if v is None:
            return float("nan")
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


class FusionV2Inference:
    """Singleton-style inference wrapper. Fail-quiet on missing model."""

    def __init__(self, model_path: str = _DEFAULT_MODEL):
        self.model_path = model_path
        self.pipeline = None
        self.feature_names: list[str] = []
        self.imputer_medians: list[float] = []
        self.cv_auc_mean: float = float("nan")
        self.n_samples: int = 0
        self.disabled = False
        self._try_load()

    def _try_load(self):
        if not os.path.exists(self.model_path):
            logger.info(f"[FusionV2] model not found at {self.model_path}; disabled")
            self.disabled = True
            return
        try:
            with open(self.model_path, "rb") as f:
                payload = pickle.load(f)
            self.pipeline = payload["pipeline"]
            self.feature_names = list(payload["feature_names"])
            self.imputer_medians = list(payload.get("imputer_medians", []))
            self.cv_auc_mean = float(payload.get("cv_auc_mean", float("nan")))
            self.n_samples = int(payload.get("n_samples", 0))
            self.disabled = False
            logger.info(
                f"[FusionV2] loaded n={self.n_samples} CV_AUC={self.cv_auc_mean:.3f} "
                f"features={len(self.feature_names)}"
            )
        except Exception as e:
            logger.warning(f"[FusionV2] failed to load: {e}")
            self.disabled = True
            self.pipeline = None

    def score_from_entry_meta(self, entry_meta: dict) -> Optional[float]:
        """Return P(win) in [0, 1] or None if disabled or extraction fails."""
        if self.disabled or self.pipeline is None:
            return None
        try:
            em = entry_meta or {}
            vec = np.array([[
                _safe_float(em.get(f)) for f in self.feature_names
            ]], dtype=np.float32)
            prob = float(self.pipeline.predict_proba(vec)[0, 1])
            return prob
        except Exception as e:
            logger.debug(f"[FusionV2] score err: {e}")
            return None


_singleton: Optional[FusionV2Inference] = None


def get_fusion_v2() -> FusionV2Inference:
    global _singleton
    if _singleton is None:
        _singleton = FusionV2Inference()
    return _singleton
