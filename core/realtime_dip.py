"""Real-time dip reference (RT_DIP_MODE).

Pure logic for computing the dip signal off a LIVE rolling reference instead of
the ~2-min-stale DexScreener snapshot anchor. Two sources feed the reference:
an in-memory per-token price buffer (built from the fresh Jupiter prices the
fast-watch already polls) and io.dexscreener bars (historical depth). Nothing
here touches the network or raises.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional, Tuple


class RollingPriceWindow:
    """Per-token ring buffer of (ts, price), evicted by age and count.

    Only positive prices are stored. window_high/window_low scan the samples
    whose ts is within `secs` of the supplied `now`. All methods are pure and
    never raise.
    """

    def __init__(self, max_age_secs: float = 86400.0, max_samples: int = 4000) -> None:
        self._samples: Deque[Tuple[float, float]] = deque()
        self._max_age = float(max_age_secs)
        self._max_samples = int(max_samples)

    def append(self, ts: float, price: float) -> None:
        try:
            ts = float(ts)
            price = float(price)
        except (TypeError, ValueError):
            return
        if not (price > 0):
            return
        self._samples.append((ts, price))
        cutoff = ts - self._max_age
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        while len(self._samples) > self._max_samples:
            self._samples.popleft()

    def window_high(self, secs: float, now: float) -> Optional[float]:
        lo = float(now) - float(secs)
        vals = [p for (t, p) in self._samples if t >= lo]
        return max(vals) if vals else None

    def window_low(self, secs: float, now: float) -> Optional[float]:
        lo = float(now) - float(secs)
        vals = [p for (t, p) in self._samples if t >= lo]
        return min(vals) if vals else None

    def newest_ts(self) -> Optional[float]:
        return self._samples[-1][0] if self._samples else None

    def __len__(self) -> int:
        return len(self._samples)
