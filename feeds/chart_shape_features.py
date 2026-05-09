"""Chart-shape features for entry-quality discrimination.

Captures the structural pattern of price action over the trailing
30/60/90 minutes that snapshot metrics (h1/h6/h24 deltas) don't see.

Hypothesis (May 8-9 session post-mortem): winners and losers can share
identical pc_h1/pc_h6 deltas yet have visually different chart shapes:
  - Winners often show a sharp pump followed by ONE clean pullback
    (V-shape) or continued consolidation, with the bot catching the dip.
  - Losers often show "round-trip" behavior — token pumped then bled
    back to its starting level, with multiple progressively-lower local
    highs (descending-staircase distribution).

Key features:
  shape_30m_max_over_entry_pct   max high in 30m as % above entry
  shape_30m_chg_pct              entry close vs 30m-ago close
  shape_30m_mins_since_max       wall-clock minutes from max bar to entry
  shape_30m_lh_count             count of consecutive lower-highs
  shape_30m_pump_bleed_score     max_over - abs(chg) — large = round trip
  shape_30m_bars_used            how many candles fed in (1m series)
  …and the same set for 60m and 90m windows.

All features computed from `candles_1m` (oldest-first). Need >= 12 bars
for the 30m series, >= 24 for 60m, >= 36 for 90m. Below the minimum
the corresponding feature subset is omitted (fail-open).

Logged-only on entry — NOT used as a filter until forward-validated.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

# Pivot-n by window: the longer the window, the wider the pivot needs
# to be so we count meaningful peaks, not 1-bar wiggles.
_PIVOT_N_BY_WINDOW = {30: 2, 60: 3, 90: 4}


def _find_pivot_highs(highs: Sequence[float], pivot_n: int) -> List[int]:
    """Return indices of local maxima where the bar is strictly higher
    than the `pivot_n` bars on each side. Edge bars (within pivot_n of
    the start/end) cannot be pivots."""
    if len(highs) < 2 * pivot_n + 1:
        return []
    out: List[int] = []
    for i in range(pivot_n, len(highs) - pivot_n):
        h = highs[i]
        if all(highs[j] < h for j in range(i - pivot_n, i)) and \
           all(highs[j] < h for j in range(i + 1, i + pivot_n + 1)):
            out.append(i)
    return out


def _consecutive_lower_high_count(pivot_highs: Sequence[float]) -> int:
    """Count of consecutive lower highs ending at the most recent pivot.
    Returns 0 if the sequence is broken (a higher pivot resets it)."""
    if len(pivot_highs) < 2:
        return 0
    cnt = 0
    for i in range(len(pivot_highs) - 1, 0, -1):
        if pivot_highs[i] < pivot_highs[i - 1]:
            cnt += 1
        else:
            break
    return cnt


def _shape_for_window(
    candles_1m: Sequence[Any], window_min: int, prefix: str
) -> Dict[str, Any]:
    """Compute the feature set for one trailing window."""
    pivot_n = _PIVOT_N_BY_WINDOW.get(window_min, 3)
    min_bars = 2 * pivot_n + 6  # need swings + buffer
    if not candles_1m or len(candles_1m) < min_bars:
        return {}
    cs = list(candles_1m[-window_min:])
    if len(cs) < min_bars:
        return {}

    entry_close = cs[-1].close
    if entry_close <= 0:
        return {}
    open_at_window_start = cs[0].open if cs[0].open > 0 else cs[0].close
    if open_at_window_start <= 0:
        return {}

    highs = [c.high for c in cs]
    max_h = max(highs)
    min_l = min(c.low for c in cs)
    max_idx = highs.index(max_h)
    mins_since_max = (len(cs) - 1) - max_idx  # bars-from-max == minutes (1m bars)

    max_over_entry_pct = (max_h / entry_close - 1) * 100
    chg_pct = (entry_close / open_at_window_start - 1) * 100
    drawdown_from_max_pct = (entry_close / max_h - 1) * 100  # always <= 0
    range_pct = (max_h / min_l - 1) * 100

    # Lower-high count using window-appropriate pivot width
    pivots_idx = _find_pivot_highs(highs, pivot_n=pivot_n)
    pivot_highs = [highs[i] for i in pivots_idx]
    lh_count = _consecutive_lower_high_count(pivot_highs)

    # Round-trip score: pumped a lot vs entry but net change is small
    pump_bleed_score = max_over_entry_pct - abs(chg_pct)

    return {
        f"{prefix}_max_over_entry_pct": round(max_over_entry_pct, 2),
        f"{prefix}_chg_pct": round(chg_pct, 2),
        f"{prefix}_mins_since_max": int(mins_since_max),
        f"{prefix}_drawdown_from_max_pct": round(drawdown_from_max_pct, 2),
        f"{prefix}_range_pct": round(range_pct, 2),
        f"{prefix}_lh_count": lh_count,
        f"{prefix}_distinct_pivots": len(pivots_idx),
        f"{prefix}_pump_bleed_score": round(pump_bleed_score, 2),
        f"{prefix}_bars_used": len(cs),
    }


def compute_chart_shape(candles_1m: Sequence[Any]) -> Dict[str, Any]:
    """Top-level entry: compute the 30m / 60m / 90m feature trio.

    `candles_1m` is the oldest-first 1-minute candle list from
    chart_data. Each window is computed independently — partial coverage
    (e.g. only 40 bars available) yields the 30m and 60m features but
    omits the 90m set."""
    out: Dict[str, Any] = {}
    out.update(_shape_for_window(candles_1m, 30, "shape_30m"))
    out.update(_shape_for_window(candles_1m, 60, "shape_60m"))
    out.update(_shape_for_window(candles_1m, 90, "shape_90m"))
    return out
