"""Chart image renderer — converts (1m, 5m, 15m) candle lists to a
3-channel 64x64 uint8 numpy array. Used identically at train time,
inference time, and forward-collection time, so train/serve skew
is structurally impossible.

Layout per channel (one TF each):
  - X-axis: 60 most-recent candles, oldest left, newest right
  - Y-axis: 64 pixels, log-normalized price range over the window
  - Body: 255 if green (close >= open), 128 if red
  - Wick: 64
  - Empty: 0

Channels:
  image[0] = 1m TF, image[1] = 5m TF, image[2] = 15m TF.
"""
from __future__ import annotations
import math
from typing import List, Optional

import numpy as np

from feeds.candle_utils import Candle

_HEIGHT = 64
_WIDTH = 64
_BARS_PER_TF = 60          # 60 candles per TF, rendered into _WIDTH=64 pixels
_MIN_BARS_PER_TF = 30      # below this, fail-open (renderer returns None)
_PX_BODY_GREEN = np.uint8(255)
_PX_BODY_RED = np.uint8(128)
_PX_WICK = np.uint8(64)


def _render_single_tf(candles: List[Candle]) -> Optional[np.ndarray]:
    """Render one timeframe's candles to a 64x64 uint8 array.
    Returns None if fewer than _MIN_BARS_PER_TF candles."""
    if not candles or len(candles) < _MIN_BARS_PER_TF:
        return None
    last = candles[-_BARS_PER_TF:]
    n = len(last)

    # Log-normalized price range across the window
    lows = [c.low for c in last if c.low > 0]
    highs = [c.high for c in last if c.high > 0]
    if not lows or not highs:
        return None
    lo, hi = min(lows), max(highs)
    if hi <= lo:
        return None
    log_lo = math.log(lo)
    log_hi = math.log(hi)
    log_range = log_hi - log_lo
    if log_range <= 0:
        return None

    img = np.zeros((_HEIGHT, _WIDTH), dtype=np.uint8)

    # Map each candle index 0..n-1 to a pixel column 0.._WIDTH-1
    # When n < _WIDTH, leftmost columns stay blank (padding).
    col_offset = _WIDTH - n  # right-aligned

    for i, c in enumerate(last):
        col = col_offset + i
        if not (0 <= col < _WIDTH):
            continue
        if c.high <= 0 or c.low <= 0 or c.open <= 0 or c.close <= 0:
            continue
        # Map price to row. Row 0 is top (high prices), row HEIGHT-1 is bottom.
        def _row(price: float) -> int:
            f = (math.log(price) - log_lo) / log_range
            r = int(round((1.0 - f) * (_HEIGHT - 1)))
            return max(0, min(_HEIGHT - 1, r))
        r_high = _row(c.high)
        r_low = _row(c.low)
        r_open = _row(c.open)
        r_close = _row(c.close)
        body_top = min(r_open, r_close)
        body_bot = max(r_open, r_close)
        is_green = c.close >= c.open
        body_px = _PX_BODY_GREEN if is_green else _PX_BODY_RED

        # Draw wick (entire vertical range)
        for r in range(r_high, r_low + 1):
            img[r, col] = _PX_WICK
        # Draw body (overwrites wick)
        for r in range(body_top, body_bot + 1):
            img[r, col] = body_px

    return img


def render_chart_image(candles_1m: List[Candle],
                        candles_5m: List[Candle],
                        candles_15m: List[Candle]) -> Optional[np.ndarray]:
    """Render three TFs into a single 3-channel 64x64 uint8 array.

    Returns None if any TF has fewer than _MIN_BARS_PER_TF (30) bars
    or if any TF fails to render (e.g., flat price range).
    """
    ch_1m = _render_single_tf(candles_1m)
    ch_5m = _render_single_tf(candles_5m)
    ch_15m = _render_single_tf(candles_15m)
    if ch_1m is None or ch_5m is None or ch_15m is None:
        return None
    return np.stack([ch_1m, ch_5m, ch_15m], axis=0)  # (3, 64, 64)
