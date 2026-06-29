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

from feeds.dexscreener_chart_format import rolling_high_from_bars


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

    def oldest_ts(self) -> Optional[float]:
        return self._samples[0][0] if self._samples else None

    def __len__(self) -> int:
        return len(self._samples)


HORIZON_SECS = {"m5": 300.0, "h1": 3600.0, "h6": 21600.0, "h24": 86400.0}

# A buffer-only horizon is emitted only when the buffer's oldest retained sample
# spans at least this fraction of the horizon window. Bars (real OHLC history)
# unlock any horizon regardless of buffer depth.
COVERAGE_FRAC = 0.5


def compute_rt_price_change(buffer, bars, fresh_price, now,
                            horizons=("m5", "h1", "h6", "h24"),
                            max_age_secs=90.0):
    """Real-time priceChange dict + coverage stamp off the window high.

    Returns ({horizon: pct}, coverage) where coverage is one of
    "BARS+BUFFER" / "BUFFER_ONLY" / "NONE". pct = (fresh/window_high - 1)*100.
    Falls to ({}, "NONE") when nothing is usable: fresh<=0, or no source
    yields a window high, or the buffer's newest sample is staler than
    max_age_secs AND there are no bars. Pure; never raises."""
    try:
        fp = float(fresh_price)
    except (TypeError, ValueError):
        return {}, "NONE"
    if not (fp > 0):
        return {}, "NONE"

    has_bars = bool(bars)
    # SCALE GUARD: io.dexscreener bars occasionally come back in a different
    # price scale than the fresh (Jupiter/USD) price — observed 28-74x off on
    # low-priced microcaps (which are exactly our dip targets). Mixing scales
    # produced garbage (e.g. a -56% dip computed as +7569%), and under enforce
    # that overwrites a real dip with a fake pump → suppresses the buy. The
    # newest bar's close is contemporaneous with `fresh_price` (both ~now), so
    # in a REAL dip the ratio is ~1 regardless of dip depth (the dip lives in
    # OLDER bars' highs, not the newest close). The observed scale errors are
    # 28-74x, so a wide [0.1, 10] band catches them with huge margin while never
    # rejecting a legitimate deep dip or a fast intra-minute move.
    if has_bars:
        try:
            newest_close = float((bars[-1] or {}).get("close") or 0)
            if newest_close > 0:
                ratio = fp / newest_close
                if ratio > 10.0 or ratio < 0.1:
                    has_bars = False
        except (TypeError, ValueError, IndexError, KeyError, AttributeError):
            pass
    newest = buffer.newest_ts() if buffer is not None else None
    buffer_stale = (newest is None) or (float(now) - float(newest) > float(max_age_secs))
    if buffer_stale and not has_bars:
        return {}, "NONE"

    now_ms = float(now) * 1000.0
    # Buffer contributes a reference only with >=2 samples — a single sample is
    # just the current price, giving a degenerate 0% dip (mirrors rolling_dip_pct).
    buf_usable = (buffer is not None and not buffer_stale and len(buffer) >= 2)
    out = {}
    bars_contributed = False
    buffer_contributed = False
    for h in horizons:
        secs = HORIZON_SECS.get(h)
        if secs is None:
            continue
        bar_hi = rolling_high_from_bars(bars, secs, now_ms) if has_bars else None
        buf_hi = buffer.window_high(secs, now) if buf_usable else None
        # A horizon is emitted only when the reference actually spans it. Bars are
        # true OHLC history -> trustworthy for any horizon. The buffer is only
        # eligible when its oldest retained sample spans most of the window.
        bar_contributed_h = bar_hi is not None and bar_hi > 0
        buf_spans = (
            buffer is not None
            and buffer.oldest_ts() is not None
            and (float(now) - buffer.oldest_ts()) >= COVERAGE_FRAC * secs
        )
        buf_eligible = buf_hi is not None and buf_hi > 0 and buf_spans
        highs = []
        if bar_contributed_h:
            highs.append(bar_hi)
        if buf_eligible:
            highs.append(buf_hi)
        if not highs:
            continue
        window_high = max(highs)
        out[h] = round((fp / window_high - 1.0) * 100.0, 6)
        if bar_contributed_h:
            bars_contributed = True
        if buf_eligible:
            buffer_contributed = True

    if not out:
        return {}, "NONE"
    if bars_contributed:
        return out, "BARS+BUFFER"
    if buffer_contributed:
        return out, "BUFFER_ONLY"
    return {}, "NONE"
