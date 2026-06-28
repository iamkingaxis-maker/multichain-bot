"""Production CNN inference singleton.

Lazy-loads weights at first call. All failures degrade gracefully —
predict() returns None on missing weights, render failure, or
inference exception. Self-disables for 60s after any uncaught
exception, then retries.

LRU cache keyed by (token_address, latest_1m_open_time) — same minute
calls return cached prediction in <1ms.
"""
from __future__ import annotations
import logging
import os
import time
from collections import OrderedDict
from typing import Dict, List, Optional

import numpy as np

from feeds.candle_utils import Candle
from feeds.chart_image_renderer import render_chart_image
from core.chart_cnn_np import ChartCNNNP, IDX_TO_CLASS
from core import np_nn

logger = logging.getLogger(__name__)

_DEFAULT_WEIGHTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "chart_cnn_v1.npz",
)
_CACHE_MAX = 512
_DISABLE_DURATION_S = 60.0
_WARN_THROTTLE_S = 300.0  # 5 min


class ChartCNNInference:
    """Singleton-style inference wrapper. Construct once at startup."""

    def __init__(self, weights_path: str = _DEFAULT_WEIGHTS):
        self.weights_path = weights_path
        self.model: Optional[ChartCNNNP] = None
        self.disabled = False
        self._disabled_until = 0.0
        self._last_warn = 0.0
        self._cache: OrderedDict = OrderedDict()
        self.cache_hits = 0
        self.predict_calls = 0
        # Eager load attempt — if missing, set disabled=True
        self._try_load()

    def _try_load(self):
        if not os.path.exists(self.weights_path):
            logger.info(
                f"[ChartCNN] weights not found at {self.weights_path}; "
                f"inference disabled (bot continues normally)"
            )
            self.disabled = True
            return
        try:
            self.model = ChartCNNNP.from_npz(self.weights_path)
            self.disabled = False
            logger.info(f"[ChartCNN] loaded weights from {self.weights_path}")
        except Exception as e:
            logger.warning(f"[ChartCNN] failed to load weights: {e}")
            self.disabled = True
            self.model = None

    def predict(self,
                token_address: str,
                candles_1m: List[Candle],
                candles_5m: List[Candle],
                candles_15m: List[Candle]) -> Optional[Dict]:
        """Run inference. Returns dict on success, None on any failure."""
        self.predict_calls += 1
        if self.disabled:
            # _disabled_until > 0 means a timed exception-disable; retry after window.
            # _disabled_until == 0 means disabled at load (missing/bad weights); stay disabled.
            if self._disabled_until > 0 and time.time() >= self._disabled_until:
                self.disabled = False  # retry window elapsed
            else:
                return None

        if self.model is None:
            return None

        # Cache key: (addr, latest 1m bar open_time)
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
            # numpy (3, 64, 64) uint8 -> numpy forward (normalizes /255 internally)
            pattern_logits, outcome_logit = self.model(img)
            pattern_probs = np_nn.softmax(pattern_logits)  # (15,)
            outcome_prob = float(np_nn.sigmoid(outcome_logit)[0])
            top_idx = int(np.argmax(pattern_probs))
            top_conf = float(pattern_probs[top_idx])
            result = {
                "pattern": IDX_TO_CLASS.get(top_idx, "unknown"),
                "pattern_conf": top_conf,
                "outcome_prob": outcome_prob,
            }
        except Exception as e:
            now = time.time()
            if now - self._last_warn > _WARN_THROTTLE_S:
                logger.warning(f"[ChartCNN] inference error: {e} (disabling 60s)")
                self._last_warn = now
            self.disabled = True
            self._disabled_until = time.time() + _DISABLE_DURATION_S
            return None

        self._cache[cache_key] = result
        if len(self._cache) > _CACHE_MAX:
            self._cache.popitem(last=False)  # LRU eviction
        return result


_singleton: Optional[ChartCNNInference] = None


def get_inference() -> ChartCNNInference:
    """Module-level accessor for the singleton."""
    global _singleton
    if _singleton is None:
        _singleton = ChartCNNInference()
    return _singleton
