"""Production inference singleton for the constrained fusion meta-model.

Loads models/fusion_constrained_v1.pkl, exposes score_from_entry_meta(em, ts_iso)
returning P(win) in [0, 1]. Returns None when the model is unavailable —
caller stamps fusion_constrained_score=None into entry_meta, no behavior change.

Used by dip_scanner to stamp `fusion_constrained_score_shadow` on every
evaluated candidate. SHADOW only — does not gate.
"""
from __future__ import annotations

import logging
import os
import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

# ScaledLR must be importable for unpickling
from models.fusion_meta import ScaledLR  # noqa: F401

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "fusion_constrained_v1.pkl",
)


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _derive_hour_ct(time_iso: str) -> float:
    if not time_iso:
        return 0.0
    try:
        dt = datetime.fromisoformat(time_iso)
        return float((dt.hour - 5) % 24)
    except Exception:
        return 0.0


class FusionConstrainedInference:
    """Singleton-style inference wrapper. Fail-quiet on missing weights."""

    def __init__(self, model_path: str = _DEFAULT_MODEL):
        self.model_path = model_path
        self.model = None
        self.feature_names: list[str] = []
        self.rug_cluster_ids: set = set()
        self.winner_cluster_ids: set = set()
        self.loo_auc: float = float("nan")
        self.n_samples: int = 0
        self.disabled = False
        self._try_load()

    def _try_load(self):
        if not os.path.exists(self.model_path):
            logger.info(f"[FusionConstrained] model not found at {self.model_path}; disabled")
            self.disabled = True
            return
        try:
            with open(self.model_path, "rb") as f:
                payload = pickle.load(f)
            self.model = payload["model"]
            self.feature_names = payload["feature_names"]
            self.rug_cluster_ids = set(payload.get("rug_cluster_ids", [19]))
            self.winner_cluster_ids = set(payload.get("winner_cluster_ids", [18]))
            self.loo_auc = float(payload.get("loo_auc", float("nan")))
            self.n_samples = int(payload.get("n_samples", 0))
            self.disabled = False
            logger.info(
                f"[FusionConstrained] loaded n={self.n_samples} AUC={self.loo_auc:.3f} "
                f"features={len(self.feature_names)}"
            )
        except Exception as e:
            logger.warning(f"[FusionConstrained] failed to load: {e}")
            self.disabled = True
            self.model = None

    def score_from_entry_meta(
        self, entry_meta: dict, time_iso: str = ""
    ) -> Optional[float]:
        """Return P(win) in [0, 1] or None if disabled or feature extraction fails."""
        if self.disabled or self.model is None:
            return None
        try:
            em = entry_meta or {}
            cluster_id = em.get("cnn_cluster_id")
            cluster_is_rug = 1.0 if cluster_id is not None and cluster_id in self.rug_cluster_ids else 0.0
            cluster_is_winner = 1.0 if cluster_id is not None and cluster_id in self.winner_cluster_ids else 0.0
            vec = np.array([[
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
            ]], dtype=np.float32)
            prob = float(self.model.predict_proba(vec)[0, 1])
            return prob
        except Exception as e:
            logger.debug(f"[FusionConstrained] score err: {e}")
            return None


_singleton: Optional[FusionConstrainedInference] = None


def get_fusion_constrained() -> FusionConstrainedInference:
    global _singleton
    if _singleton is None:
        _singleton = FusionConstrainedInference()
    return _singleton
