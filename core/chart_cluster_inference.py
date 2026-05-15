"""Production cluster-classifier inference singleton.

Loads the ChartEncoder + saved k-means cluster centers, classifies each
new chart into one of N clusters via nearest-center matching. The
encoder embeds the 3x64x64 image to 64-dim, then we find the closest
of the 20 cluster centers (Euclidean distance).

Used by filter_cluster_19_rug — block entries when the chart shape
matches Cluster 19 (the rug cluster discovered by autoencoder + k-means).
Cluster 19 has 67% historical rug rate and -18.5% avg P&L on 6 samples.

If encoder weights or cluster centers are missing, the singleton
gracefully disables and `classify()` returns None — bot continues normally.
"""
from __future__ import annotations
import logging
import os
import time
from collections import OrderedDict
from typing import List, Optional

import numpy as np
import torch

from feeds.candle_utils import Candle
from feeds.chart_image_renderer import render_chart_image
from models.chart_autoencoder import ChartEncoder

logger = logging.getLogger(__name__)

_DEFAULT_ENCODER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "chart_encoder_v1.pt",
)
_DEFAULT_CENTERS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "chart_cluster_centers_v1.npy",
)
_CACHE_MAX = 512
_DISABLE_DURATION_S = 60.0
_WARN_THROTTLE_S = 300.0

# Clusters identified as "rug shapes" in the 2026-05-15 analysis.
# Cluster 19: 67% rug rate (4/6), avg P&L -18.5%, all trades losers.
# Adding more clusters requires re-running rug_predictor_analysis.py
# with strategy-cap data and updating this set.
RUG_CLUSTERS = {19}


class ChartClusterInference:
    """Singleton-style cluster classifier."""

    def __init__(self,
                 encoder_path: str = _DEFAULT_ENCODER,
                 centers_path: str = _DEFAULT_CENTERS):
        self.encoder_path = encoder_path
        self.centers_path = centers_path
        self.encoder: Optional[ChartEncoder] = None
        self.centers: Optional[np.ndarray] = None  # (N, 64)
        self.disabled = False
        self._disabled_until = 0.0
        self._last_warn = 0.0
        self._cache: OrderedDict = OrderedDict()
        self.cache_hits = 0
        self.predict_calls = 0
        self._try_load()

    def _try_load(self):
        if not os.path.exists(self.encoder_path):
            logger.info(
                f"[ChartCluster] encoder not found at {self.encoder_path}; disabled"
            )
            self.disabled = True
            return
        if not os.path.exists(self.centers_path):
            logger.info(
                f"[ChartCluster] cluster centers not found at {self.centers_path}; disabled"
            )
            self.disabled = True
            return
        try:
            self.encoder = ChartEncoder()
            sd = torch.load(self.encoder_path, map_location="cpu", weights_only=True)
            self.encoder.load_state_dict(sd)
            self.encoder.eval()
            self.centers = np.load(self.centers_path).astype(np.float32)
            self.disabled = False
            logger.info(
                f"[ChartCluster] loaded encoder + {self.centers.shape[0]} cluster centers; "
                f"rug clusters: {sorted(RUG_CLUSTERS)}"
            )
        except Exception as e:
            logger.warning(f"[ChartCluster] failed to load: {e}")
            self.disabled = True
            self.encoder = None
            self.centers = None

    def classify(self,
                 token_address: str,
                 candles_1m: List[Candle],
                 candles_5m: List[Candle],
                 candles_15m: List[Candle]) -> Optional[int]:
        """Classify chart into nearest cluster_id. Returns int or None on failure."""
        self.predict_calls += 1
        if self.disabled:
            if self._disabled_until > 0 and time.time() >= self._disabled_until:
                self.disabled = False
            else:
                return None
        if self.encoder is None or self.centers is None:
            return None
        if not candles_1m:
            return None
        cache_key = (token_address, candles_1m[-1].open_time)
        if cache_key in self._cache:
            self.cache_hits += 1
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        try:
            img = render_chart_image(candles_1m, candles_5m, candles_15m)
            if img is None:
                return None
            tensor = torch.from_numpy(img).unsqueeze(0).float() / 255.0
            with torch.no_grad():
                embedding = self.encoder(tensor).cpu().numpy()[0]  # (64,)
            # Nearest-center via Euclidean distance
            dists = np.linalg.norm(self.centers - embedding, axis=1)
            cluster_id = int(np.argmin(dists))
        except Exception as e:
            now = time.time()
            if now - self._last_warn > _WARN_THROTTLE_S:
                logger.warning(f"[ChartCluster] classify err: {e} (disabling 60s)")
                self._last_warn = now
            self.disabled = True
            self._disabled_until = time.time() + _DISABLE_DURATION_S
            return None

        self._cache[cache_key] = cluster_id
        if len(self._cache) > _CACHE_MAX:
            self._cache.popitem(last=False)
        return cluster_id

    def is_rug_cluster(self, cluster_id: Optional[int]) -> bool:
        return cluster_id is not None and cluster_id in RUG_CLUSTERS


_singleton: Optional[ChartClusterInference] = None


def get_cluster_inference() -> ChartClusterInference:
    global _singleton
    if _singleton is None:
        _singleton = ChartClusterInference()
    return _singleton
