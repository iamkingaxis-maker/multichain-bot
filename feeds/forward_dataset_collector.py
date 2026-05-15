"""Forward-collected chart dataset. Called on every scanner candidate
that has chart_data available. Dumps image + partial label; outcome is
appended later when the trade closes.

Disk-space guard: skips writes when free space < 5% (throttled WARNING).
"""
from __future__ import annotations
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from feeds.candle_utils import Candle
from feeds.chart_image_renderer import render_chart_image

logger = logging.getLogger(__name__)

_DEFAULT_ROOT = "/data/cnn_dataset/forward"
_DISK_GUARD_FREE_PCT = 0.05  # require >=5% free
_WARN_THROTTLE_S = 300.0


def _safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", s)


class ForwardDatasetCollector:
    def __init__(self, root_dir: str = _DEFAULT_ROOT):
        self.root_dir = Path(root_dir)
        self._last_disk_warn = 0.0

    def _disk_has_space(self) -> bool:
        try:
            self.root_dir.mkdir(parents=True, exist_ok=True)
            total, used, free = shutil.disk_usage(str(self.root_dir))
            free_pct = free / total if total > 0 else 1.0
            if free_pct < _DISK_GUARD_FREE_PCT:
                now = time.time()
                if now - self._last_disk_warn > _WARN_THROTTLE_S:
                    logger.warning(
                        f"[forward_collector] disk <5% free; dropping writes"
                    )
                    self._last_disk_warn = now
                return False
            return True
        except Exception:
            return True  # fail-open

    def _paths(self, token_address: str, ts_iso: str) -> tuple:
        date = ts_iso[:10]  # YYYY-MM-DD
        date_dir = self.root_dir / date
        date_dir.mkdir(parents=True, exist_ok=True)
        base = f"{_safe_filename(token_address)}_{_safe_filename(ts_iso)}"
        return date_dir / f"{base}.npy", date_dir / f"{base}.json"

    def dump_snapshot(self,
                      token_address: str,
                      ts_iso: str,
                      candles_1m: List[Candle],
                      candles_5m: List[Candle],
                      candles_15m: List[Candle],
                      context: Dict) -> bool:
        """Write a partial-label snapshot. Returns True on success."""
        if not self._disk_has_space():
            return False
        try:
            img = render_chart_image(candles_1m, candles_5m, candles_15m)
            if img is None:
                return False
            npy_path, json_path = self._paths(token_address, ts_iso)
            np.save(npy_path, img)
            label = {
                "addr": token_address,
                "ts": ts_iso,
                "pattern_label": None,  # filled at training time from chart_reader
                "outcome_label": None,  # filled by update_outcome on trade close
                "outcome_pnl_pct": None,
                "context": context,
            }
            with open(json_path, "w") as f:
                json.dump(label, f)
            return True
        except Exception as e:
            logger.debug(f"[forward_collector] dump err: {e}")
            return False

    def update_outcome(self,
                       token_address: str,
                       ts_iso: str,
                       outcome_label: int,
                       outcome_pnl_pct: float) -> bool:
        """Find the matching partial label and append outcome fields."""
        try:
            _, json_path = self._paths(token_address, ts_iso)
            if not json_path.exists():
                return False
            with open(json_path) as f:
                label = json.load(f)
            label["outcome_label"] = int(outcome_label)
            label["outcome_pnl_pct"] = float(outcome_pnl_pct)
            with open(json_path, "w") as f:
                json.dump(label, f)
            return True
        except Exception as e:
            logger.debug(f"[forward_collector] update err: {e}")
            return False


_singleton: Optional[ForwardDatasetCollector] = None


def get_collector() -> ForwardDatasetCollector:
    global _singleton
    if _singleton is None:
        # Use DATA_DIR env if set (Railway), else fallback to relative path
        data_dir = os.environ.get("DATA_DIR", "/data")
        root = os.path.join(data_dir, "cnn_dataset/forward")
        _singleton = ForwardDatasetCollector(root_dir=root)
    return _singleton
