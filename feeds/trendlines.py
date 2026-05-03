"""
Phase 7 of chart-reading rebuild — diagonal trendlines, breakouts, channels.

Goes beyond the static horizontal levels of Phase 3 (support_resistance.py).
A trendline is a diagonal line that price has been respecting over time —
the "trend skeleton" of a chart. Breakouts of trendlines often produce
the most reliable directional moves traders look for.

Algorithm:
  1. Use the existing fractal pivots (Phase 3) as anchor points.
  2. Try every pair of pivots as a candidate line; count how many OTHER
     pivots fall within tolerance% of that line.
  3. Score each candidate line by:
       touches  — number of pivots on the line (more = more validated)
       recency  — index of the last touch (more recent = still relevant)
       r_squared — fit quality across all touched pivots
  4. The best-scoring line per direction (resistance from highs, support
     from lows) becomes the "active" trendline for that timeframe.

Breakout detection:
  Current candle's close above the resistance trendline value at the
  current index = bullish breakout. Below the support trendline =
  breakdown. Both flagged separately, with a volume-confirmation flag
  (current candle volume >= 1.5x trailing average) to filter false
  breakouts on dead volume.

Channel detection:
  When both trendlines exist with similar slopes (parallel within 30%),
  price is in a tradeable channel. Position within the channel
  (channel_position_pct) goes 0 (at support) → 100 (at resistance).
  Ascending channel near support = high-probability bounce setup;
  descending channel near resistance = high-probability rejection setup.

Output verdict (single tag for downstream filtering):
  BREAKOUT_UP   — broken above resistance with volume
  BREAKDOWN     — broken below support with volume
  PASS          — in ascending channel, near support (bullish setup)
  BLOCK         — in descending channel, near resistance (bearish setup)
  NEUTRAL       — none of the above

This module is pure logic: input is a list of Candle objects, output
is the analysis dict. No I/O, no caching — caller (chart_reader) runs
it on whichever timeframe(s) it wants.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple

from feeds.candle_utils import Candle
from feeds.support_resistance import find_pivot_highs, find_pivot_lows


# ── Line math ────────────────────────────────────────────────────────

def _line_value(slope: float, intercept: float, x: int) -> float:
    """y = slope*x + intercept."""
    return slope * x + intercept


def _fit_line_through(p1: Tuple[int, float, float],
                      p2: Tuple[int, float, float]) -> Tuple[float, float]:
    """Build the line through two pivots. Returns (slope, intercept)."""
    x1, y1, _ = p1
    x2, y2, _ = p2
    if x2 == x1:
        return 0.0, y1
    slope = (y2 - y1) / (x2 - x1)
    intercept = y1 - slope * x1
    return slope, intercept


def _count_touches(pivots: List[Tuple[int, float, float]],
                   slope: float, intercept: float,
                   tolerance_pct: float) -> Tuple[int, int]:
    """Count pivots within tolerance% of the line. Returns (touch_count, last_touch_x)."""
    touches = 0
    last_x = -1
    for x, y, _ in pivots:
        line_y = _line_value(slope, intercept, x)
        if line_y <= 0:
            continue
        if abs(y - line_y) / line_y * 100 <= tolerance_pct:
            touches += 1
            if x > last_x:
                last_x = x
    return touches, last_x


def _r_squared(pivots: List[Tuple[int, float, float]],
               slope: float, intercept: float) -> float:
    """Coefficient of determination for the line fit across all pivots."""
    if len(pivots) < 2:
        return 0.0
    ys = [p[1] for p in pivots]
    mean_y = sum(ys) / len(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot == 0:
        return 0.0
    ss_res = sum((p[1] - _line_value(slope, intercept, p[0])) ** 2 for p in pivots)
    return max(0.0, 1.0 - ss_res / ss_tot)


# ── Trendline finder ────────────────────────────────────────────────

def find_best_trendline(
    pivots: List[Tuple[int, float, float]],
    *,
    tolerance_pct: float = 1.5,
    min_touches: int = 2,
) -> Optional[Dict[str, Any]]:
    """Find the best-scoring trendline through the pivots.

    Score = touches × (1 + recency_weight) × sqrt(r_squared).
    Recency weight rewards lines whose most recent touch is near the
    end of the window — old lines that haven't been touched in a while
    decay in relevance.
    """
    if len(pivots) < min_touches:
        return None

    max_x = max(p[0] for p in pivots)
    if max_x <= 0:
        return None

    best = None
    best_score = -1.0

    for i in range(len(pivots)):
        for j in range(i + 1, len(pivots)):
            slope, intercept = _fit_line_through(pivots[i], pivots[j])
            touches, last_x = _count_touches(pivots, slope, intercept, tolerance_pct)
            if touches < min_touches:
                continue
            r2 = _r_squared(pivots, slope, intercept)
            recency = last_x / max_x  # 0..1
            score = touches * (1.0 + recency) * (r2 ** 0.5)
            if score > best_score:
                best_score = score
                best = {
                    "slope": slope,
                    "intercept": intercept,
                    "touches": touches,
                    "r_squared": round(r2, 3),
                    "last_touch_index": last_x,
                    "anchor_indices": [pivots[i][0], pivots[j][0]],
                    "score": round(score, 3),
                }
    return best


# ── Breakout detection ─────────────────────────────────────────────

def detect_breakout(
    trendline: Optional[Dict[str, Any]],
    last_candle: Candle,
    current_index: int,
    avg_volume: float,
    direction: str,
    *,
    volume_mult: float = 1.5,
) -> Dict[str, Any]:
    """Did the last candle's close break the trendline?

    direction='above' tests resistance break (close > line value).
    direction='below' tests support breakdown (close < line value).
    Volume-confirmed when last_candle.volume >= avg_volume * volume_mult.
    """
    blank = {"broken": False, "volume_confirmed": False, "magnitude_pct": None}
    if not trendline:
        return blank
    line_y = _line_value(trendline["slope"], trendline["intercept"], current_index)
    if line_y <= 0:
        return blank

    close = last_candle.close
    if direction == "above":
        broken = close > line_y
        magnitude = ((close / line_y) - 1.0) * 100 if broken else 0.0
    elif direction == "below":
        broken = close < line_y
        magnitude = ((line_y / close) - 1.0) * 100 if (broken and close > 0) else 0.0
    else:
        return blank

    vol_confirmed = avg_volume > 0 and last_candle.volume >= avg_volume * volume_mult
    return {
        "broken": broken,
        "magnitude_pct": round(magnitude, 3) if broken else 0.0,
        "volume_confirmed": vol_confirmed,
        "current_volume": round(last_candle.volume, 2),
        "avg_volume": round(avg_volume, 2),
        "volume_ratio": round(last_candle.volume / avg_volume, 3) if avg_volume > 0 else 0.0,
    }


# ── Channel detection ──────────────────────────────────────────────

def detect_channel(
    resistance_tl: Optional[Dict[str, Any]],
    support_tl: Optional[Dict[str, Any]],
    *,
    slope_match_pct: float = 30.0,
) -> Optional[Dict[str, Any]]:
    """If resistance and support trendlines have similar slopes, it's a channel.

    Slopes must be within slope_match_pct% of each other AND have the same
    sign (both ascending or both descending). Special case: both ~0 = horizontal range.
    """
    if not resistance_tl or not support_tl:
        return None
    rs = resistance_tl["slope"]
    ss = support_tl["slope"]

    flat_thresh = 1e-9
    if abs(rs) < flat_thresh and abs(ss) < flat_thresh:
        return {"slope_type": "horizontal", "resistance_slope": 0.0, "support_slope": 0.0}
    if abs(rs) < flat_thresh or abs(ss) < flat_thresh:
        return None  # one flat one diagonal — not a channel
    if (rs > 0) != (ss > 0):
        return None  # opposite signs (converging wedge, not channel)

    bigger = max(abs(rs), abs(ss))
    diff_pct = abs(rs - ss) / bigger * 100 if bigger > 0 else 100
    if diff_pct > slope_match_pct:
        return None

    slope_type = "ascending" if rs > 0 else "descending"
    return {
        "slope_type": slope_type,
        "resistance_slope": round(rs, 12),
        "support_slope": round(ss, 12),
        "slope_match_pct": round(diff_pct, 2),
    }


# ── Main analysis ──────────────────────────────────────────────────

def analyze(
    candles: List[Candle],
    *,
    pivot_n: int = 3,
    tolerance_pct: float = 1.5,
    volume_mult: float = 1.5,
    vol_avg_window: int = 10,
) -> Dict[str, Any]:
    """Full trendline analysis on a candle series.

    Returns:
      resistance_trendline    dict (slope/intercept/touches/r_squared) or None
      support_trendline       same
      pct_to_resistance       % current is below resistance line (positive = headroom)
      pct_to_support          % current is above support line (positive = cushion)
      breakout_above_resistance   bool — current close > resistance line
      breakout_above_volume_confirmed   bool
      breakout_above_magnitude_pct      how far above
      breakout_below_support  bool
      breakout_below_volume_confirmed   bool
      breakout_below_magnitude_pct
      in_channel              bool — both lines parallel
      channel_slope_type      ascending / descending / horizontal / None
      channel_position_pct    0 (at support) .. 100 (at resistance)
      trendline_verdict       BREAKOUT_UP / BREAKDOWN / PASS / BLOCK / NEUTRAL
    """
    blank = {
        "resistance_trendline": None,
        "support_trendline": None,
        "pct_to_resistance": None,
        "pct_to_support": None,
        "breakout_above_resistance": False,
        "breakout_above_volume_confirmed": False,
        "breakout_above_magnitude_pct": None,
        "breakout_below_support": False,
        "breakout_below_volume_confirmed": False,
        "breakout_below_magnitude_pct": None,
        "in_channel": False,
        "channel_slope_type": None,
        "channel_position_pct": None,
        "trendline_verdict": "?",
    }
    if not candles or len(candles) < (2 * pivot_n + 2):
        return blank

    pivot_highs = find_pivot_highs(candles, n=pivot_n)
    pivot_lows = find_pivot_lows(candles, n=pivot_n)

    resistance_tl = find_best_trendline(pivot_highs, tolerance_pct=tolerance_pct)
    support_tl = find_best_trendline(pivot_lows, tolerance_pct=tolerance_pct)

    last = candles[-1]
    cur = last.close
    cur_idx = len(candles) - 1

    # Distance from current price to each line at the current candle's index
    pct_to_resistance = None
    if resistance_tl:
        rl = _line_value(resistance_tl["slope"], resistance_tl["intercept"], cur_idx)
        if rl > 0 and cur > 0:
            # positive = price below resistance (headroom)
            pct_to_resistance = round(((rl / cur) - 1.0) * 100, 3)

    pct_to_support = None
    if support_tl:
        sl = _line_value(support_tl["slope"], support_tl["intercept"], cur_idx)
        if sl > 0 and cur > 0:
            # positive = price above support (cushion)
            pct_to_support = round(((cur / sl) - 1.0) * 100, 3)

    # Volume baseline (last N candles)
    window = candles[-vol_avg_window:] if len(candles) >= vol_avg_window else candles
    avg_vol = sum(c.volume for c in window) / max(len(window), 1)

    breakout_above = detect_breakout(resistance_tl, last, cur_idx, avg_vol, "above", volume_mult=volume_mult)
    breakout_below = detect_breakout(support_tl, last, cur_idx, avg_vol, "below", volume_mult=volume_mult)

    channel = detect_channel(resistance_tl, support_tl)
    in_channel = channel is not None
    channel_pos = None
    if channel and resistance_tl and support_tl and cur > 0:
        rl = _line_value(resistance_tl["slope"], resistance_tl["intercept"], cur_idx)
        sl = _line_value(support_tl["slope"], support_tl["intercept"], cur_idx)
        if rl > sl > 0:
            channel_pos = round(max(0.0, min(100.0, (cur - sl) / (rl - sl) * 100)), 1)

    # Verdict
    verdict = "NEUTRAL"
    if breakout_above["broken"] and breakout_above["volume_confirmed"]:
        verdict = "BREAKOUT_UP"
    elif breakout_below["broken"] and breakout_below["volume_confirmed"]:
        verdict = "BREAKDOWN"
    elif in_channel and channel_pos is not None and channel:
        slope_type = channel["slope_type"]
        if channel_pos < 25 and slope_type == "ascending":
            verdict = "PASS"
        elif channel_pos > 75 and slope_type == "descending":
            verdict = "BLOCK"

    def _trim(tl):
        if not tl:
            return None
        return {
            "slope": tl["slope"],
            "intercept": round(tl["intercept"], 10),
            "touches": tl["touches"],
            "r_squared": tl["r_squared"],
            "last_touch_index": tl["last_touch_index"],
        }

    return {
        "resistance_trendline": _trim(resistance_tl),
        "support_trendline": _trim(support_tl),
        "pct_to_resistance": pct_to_resistance,
        "pct_to_support": pct_to_support,
        "breakout_above_resistance": breakout_above["broken"],
        "breakout_above_volume_confirmed": breakout_above["volume_confirmed"],
        "breakout_above_magnitude_pct": breakout_above["magnitude_pct"],
        "breakout_above_volume_ratio": breakout_above.get("volume_ratio"),
        "breakout_below_support": breakout_below["broken"],
        "breakout_below_volume_confirmed": breakout_below["volume_confirmed"],
        "breakout_below_magnitude_pct": breakout_below["magnitude_pct"],
        "breakout_below_volume_ratio": breakout_below.get("volume_ratio"),
        "in_channel": in_channel,
        "channel_slope_type": channel["slope_type"] if channel else None,
        "channel_position_pct": channel_pos,
        "trendline_verdict": verdict,
    }
