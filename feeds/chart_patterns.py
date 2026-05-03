"""
Phase 5 of chart-reading rebuild — multi-candle chart pattern recognition.

Pattern detection at the structural level (above individual candles).
These are the patterns memecoin traders look for AFTER reading the
broader trend and S/R structure: continuation patterns inside an
uptrend, reversal patterns at extremes.

Detects:
  bull_flag       horizontal/declining consolidation after a sharp
                  pump; resolves up if the channel breaks
  bear_flag       horizontal/rising consolidation after a sharp dump;
                  resolves down if breaks down
  ascending_triangle    flat resistance + rising lows; bullish
  descending_triangle   flat support + falling highs; bearish
  symmetrical_triangle  converging highs and lows; direction unclear
  double_bottom         two lows at similar price + intervening high;
                        bullish reversal
  double_top            two highs at similar price + intervening low;
                        bearish reversal

Input: a list of Candle objects (typically 5m or 15m timeframe).
Output: dict with most-likely pattern name, confidence (0-100), and
key supporting metrics (channel slope, base width, volume profile).

Confidence calibration:
  >=70  high-confidence pattern; consider as strong directional bias
  40-69 weak pattern; informational only
  <40   noise; pattern not present

Validation discipline:
  This phase is the MOST prone to false positives. Each pattern has
  conservative thresholds. We don't enforce any of these as filters
  yet — they ship as shadow features. After ~50-100 forward trades
  with the patterns labeled, we'll measure whether labeled patterns
  actually correlate with outcomes.

Architecture:
  Each pattern has a `detect_X` function returning (confidence, dict).
  The orchestrator `detect_patterns` runs all detectors and returns
  the highest-confidence one (or none, if all below threshold).
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple

from feeds.candle_utils import Candle
from feeds.support_resistance import find_pivot_highs, find_pivot_lows


# ── Helpers ──────────────────────────────────────────────────────────

def _slope(prices: List[float]) -> float:
    """Linear regression slope of a price series. Returns slope per
    candle. Used to detect rising/falling/flat trendlines."""
    n = len(prices)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2
    y_mean = sum(prices) / n
    num = sum((i - x_mean) * (p - y_mean) for i, p in enumerate(prices))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den > 0 else 0.0


def _slope_pct_per_candle(prices: List[float]) -> float:
    """Slope expressed as % of mean price per candle — comparable
    across price ranges."""
    if not prices:
        return 0.0
    mean_p = sum(prices) / len(prices)
    if mean_p <= 0:
        return 0.0
    return _slope(prices) / mean_p * 100


def _pct_change(start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    return (end - start) / start * 100


# ── Bull / Bear flag ─────────────────────────────────────────────────

def detect_bull_flag(candles: List[Candle]) -> Tuple[float, Dict[str, Any]]:
    """A bull flag is:
      1. Sharp upward "pole" (>=10% gain over a few candles)
      2. Followed by a "flag" — sideways or slightly-down consolidation
         (< 5% range, slope between -2% and +1% per candle)
    The pole + flag together typically span 15-30 candles.

    Returns (confidence, metadata).
    """
    n = len(candles)
    if n < 15:
        return 0.0, {}

    # Look at last 25 candles (max). Split into pole (first 5-10) +
    # flag (rest). Try splits 5,7,10 and pick the best-fit.
    best_conf = 0.0
    best_meta: Dict[str, Any] = {}
    window = candles[-min(25, n):]
    for pole_size in (5, 7, 10):
        if pole_size + 5 > len(window):
            continue
        pole = window[:pole_size]
        flag = window[pole_size:]
        pole_change = _pct_change(pole[0].open, pole[-1].close)
        if pole_change < 10:
            continue
        flag_closes = [c.close for c in flag]
        flag_high = max(c.high for c in flag)
        flag_low = min(c.low for c in flag)
        flag_range_pct = _pct_change(flag_low, flag_high)
        flag_slope = _slope_pct_per_candle(flag_closes)
        # Flag should be tight (< 5% range) and slope between -2 and +1
        if flag_range_pct < 5 and -2.0 <= flag_slope <= 1.0:
            # Confidence: scales with pole strength + flag tightness
            conf = min(100.0, (pole_change - 10) * 3 + (5 - flag_range_pct) * 8)
            if flag_slope < 0:
                conf += 5  # slight downslope is classic flag
            if conf > best_conf:
                best_conf = conf
                best_meta = {
                    "pole_size": pole_size,
                    "pole_change_pct": round(pole_change, 2),
                    "flag_size": len(flag),
                    "flag_range_pct": round(flag_range_pct, 2),
                    "flag_slope_pct_per_candle": round(flag_slope, 3),
                }
    return best_conf, best_meta


def detect_bear_flag(candles: List[Candle]) -> Tuple[float, Dict[str, Any]]:
    """Inverse of bull_flag — sharp drop pole + sideways/upward flag."""
    n = len(candles)
    if n < 15:
        return 0.0, {}
    best_conf = 0.0
    best_meta: Dict[str, Any] = {}
    window = candles[-min(25, n):]
    for pole_size in (5, 7, 10):
        if pole_size + 5 > len(window):
            continue
        pole = window[:pole_size]
        flag = window[pole_size:]
        pole_change = _pct_change(pole[0].open, pole[-1].close)
        if pole_change > -10:
            continue
        flag_closes = [c.close for c in flag]
        flag_high = max(c.high for c in flag)
        flag_low = min(c.low for c in flag)
        flag_range_pct = _pct_change(flag_low, flag_high)
        flag_slope = _slope_pct_per_candle(flag_closes)
        if flag_range_pct < 5 and -1.0 <= flag_slope <= 2.0:
            conf = min(100.0, (-pole_change - 10) * 3 + (5 - flag_range_pct) * 8)
            if flag_slope > 0:
                conf += 5
            if conf > best_conf:
                best_conf = conf
                best_meta = {
                    "pole_size": pole_size,
                    "pole_change_pct": round(pole_change, 2),
                    "flag_size": len(flag),
                    "flag_range_pct": round(flag_range_pct, 2),
                    "flag_slope_pct_per_candle": round(flag_slope, 3),
                }
    return best_conf, best_meta


# ── Triangle patterns ────────────────────────────────────────────────

def detect_ascending_triangle(candles: List[Candle], pivot_n: int = 2) -> Tuple[float, Dict[str, Any]]:
    """Flat resistance (highs at similar level) + rising lows.
    Bullish — typically resolves UP."""
    if len(candles) < 20:
        return 0.0, {}
    window = candles[-min(40, len(candles)):]
    highs = find_pivot_highs(window, n=pivot_n)
    lows = find_pivot_lows(window, n=pivot_n)
    if len(highs) < 2 or len(lows) < 2:
        return 0.0, {}

    high_prices = [p[1] for p in highs]
    low_prices = [p[1] for p in lows]
    high_mean = sum(high_prices) / len(high_prices)
    high_spread_pct = _pct_change(min(high_prices), max(high_prices))
    low_slope = _slope_pct_per_candle(low_prices)

    # Highs flat (< 2% spread) + lows rising (slope > 0.5% per pivot)
    if high_spread_pct < 2 and low_slope > 0.5:
        conf = min(100.0, (2 - high_spread_pct) * 30 + low_slope * 20)
        return conf, {
            "resistance_level": round(high_mean, 8),
            "high_spread_pct": round(high_spread_pct, 2),
            "low_slope_pct_per_candle": round(low_slope, 3),
            "high_count": len(high_prices),
            "low_count": len(low_prices),
        }
    return 0.0, {}


def detect_descending_triangle(candles: List[Candle], pivot_n: int = 2) -> Tuple[float, Dict[str, Any]]:
    """Flat support + falling highs. Bearish — typically resolves DOWN."""
    if len(candles) < 20:
        return 0.0, {}
    window = candles[-min(40, len(candles)):]
    highs = find_pivot_highs(window, n=pivot_n)
    lows = find_pivot_lows(window, n=pivot_n)
    if len(highs) < 2 or len(lows) < 2:
        return 0.0, {}

    high_prices = [p[1] for p in highs]
    low_prices = [p[1] for p in lows]
    low_mean = sum(low_prices) / len(low_prices)
    low_spread_pct = _pct_change(min(low_prices), max(low_prices))
    high_slope = _slope_pct_per_candle(high_prices)

    # Lows flat (< 2% spread) + highs falling (slope < -0.5% per pivot)
    if low_spread_pct < 2 and high_slope < -0.5:
        conf = min(100.0, (2 - low_spread_pct) * 30 + (-high_slope) * 20)
        return conf, {
            "support_level": round(low_mean, 8),
            "low_spread_pct": round(low_spread_pct, 2),
            "high_slope_pct_per_candle": round(high_slope, 3),
            "high_count": len(high_prices),
            "low_count": len(low_prices),
        }
    return 0.0, {}


def detect_symmetrical_triangle(candles: List[Candle], pivot_n: int = 2) -> Tuple[float, Dict[str, Any]]:
    """Converging highs and lows — direction unclear, often
    continuation of prior trend."""
    if len(candles) < 20:
        return 0.0, {}
    window = candles[-min(40, len(candles)):]
    highs = find_pivot_highs(window, n=pivot_n)
    lows = find_pivot_lows(window, n=pivot_n)
    if len(highs) < 2 or len(lows) < 2:
        return 0.0, {}

    high_prices = [p[1] for p in highs]
    low_prices = [p[1] for p in lows]
    high_slope = _slope_pct_per_candle(high_prices)
    low_slope = _slope_pct_per_candle(low_prices)

    # Highs falling AND lows rising — converging
    if high_slope < -0.3 and low_slope > 0.3:
        conf = min(100.0, (-high_slope) * 25 + low_slope * 25)
        return conf, {
            "high_slope_pct_per_candle": round(high_slope, 3),
            "low_slope_pct_per_candle": round(low_slope, 3),
            "high_count": len(high_prices),
            "low_count": len(low_prices),
        }
    return 0.0, {}


# ── Double bottom / top ──────────────────────────────────────────────

def detect_double_bottom(candles: List[Candle], pivot_n: int = 3) -> Tuple[float, Dict[str, Any]]:
    """Two lows at similar price ($1% apart) with an intervening high
    at least 3% above. Bullish reversal pattern."""
    if len(candles) < 15:
        return 0.0, {}
    window = candles[-min(60, len(candles)):]
    lows = find_pivot_lows(window, n=pivot_n)
    if len(lows) < 2:
        return 0.0, {}
    # Take the two most-recent pivot lows
    lows_sorted = sorted(lows, key=lambda p: p[0])  # by index
    last_two = lows_sorted[-2:]
    (idx1, p1, _), (idx2, p2, _) = last_two
    if p1 <= 0 or p2 <= 0:
        return 0.0, {}
    spread_pct = abs(p1 - p2) / ((p1 + p2) / 2) * 100
    if spread_pct > 2.0:  # more than 2% apart — not the same level
        return 0.0, {}
    # Intervening high
    between = window[idx1:idx2 + 1]
    if not between:
        return 0.0, {}
    peak = max(c.high for c in between)
    base = (p1 + p2) / 2
    rise_pct = _pct_change(base, peak)
    if rise_pct < 3:  # too shallow
        return 0.0, {}
    # Confirmation: current price above the intervening high?
    current_close = candles[-1].close
    confirmed = current_close > peak
    conf = min(100.0, (3 - spread_pct) * 20 + rise_pct * 3)
    if confirmed:
        conf += 15
    return conf, {
        "low1_price": round(p1, 8),
        "low2_price": round(p2, 8),
        "low_spread_pct": round(spread_pct, 2),
        "intervening_peak": round(peak, 8),
        "rise_pct": round(rise_pct, 2),
        "confirmed_breakout": confirmed,
    }


def detect_double_top(candles: List[Candle], pivot_n: int = 3) -> Tuple[float, Dict[str, Any]]:
    """Two highs at similar price + intervening low. Bearish reversal."""
    if len(candles) < 15:
        return 0.0, {}
    window = candles[-min(60, len(candles)):]
    highs = find_pivot_highs(window, n=pivot_n)
    if len(highs) < 2:
        return 0.0, {}
    highs_sorted = sorted(highs, key=lambda p: p[0])
    last_two = highs_sorted[-2:]
    (idx1, p1, _), (idx2, p2, _) = last_two
    if p1 <= 0 or p2 <= 0:
        return 0.0, {}
    spread_pct = abs(p1 - p2) / ((p1 + p2) / 2) * 100
    if spread_pct > 2.0:
        return 0.0, {}
    between = window[idx1:idx2 + 1]
    if not between:
        return 0.0, {}
    trough = min(c.low for c in between)
    base = (p1 + p2) / 2
    drop_pct = _pct_change(base, trough)
    if drop_pct > -3:
        return 0.0, {}
    current_close = candles[-1].close
    confirmed = current_close < trough
    conf = min(100.0, (3 - spread_pct) * 20 + (-drop_pct) * 3)
    if confirmed:
        conf += 15
    return conf, {
        "high1_price": round(p1, 8),
        "high2_price": round(p2, 8),
        "high_spread_pct": round(spread_pct, 2),
        "intervening_trough": round(trough, 8),
        "drop_pct": round(drop_pct, 2),
        "confirmed_breakdown": confirmed,
    }


# ── Pattern direction tagging ───────────────────────────────────────

_PATTERN_DIR = {
    "bull_flag": "bullish",
    "bear_flag": "bearish",
    "ascending_triangle": "bullish",
    "descending_triangle": "bearish",
    "symmetrical_triangle": "neutral",
    "double_bottom": "bullish",
    "double_top": "bearish",
}


def detect_patterns(candles: List[Candle], min_confidence: float = 40.0) -> Dict[str, Any]:
    """Run all detectors, return the highest-confidence pattern (if any).

    Output:
      pattern              name of best-matching pattern (or None)
      direction            'bullish' / 'bearish' / 'neutral' / 'none'
      confidence           0-100
      meta                 supporting numbers from the detector
      all_patterns         dict of pattern_name → confidence (every
                           detector that scored above zero)
    """
    detectors = [
        ("bull_flag", detect_bull_flag),
        ("bear_flag", detect_bear_flag),
        ("ascending_triangle", detect_ascending_triangle),
        ("descending_triangle", detect_descending_triangle),
        ("symmetrical_triangle", detect_symmetrical_triangle),
        ("double_bottom", detect_double_bottom),
        ("double_top", detect_double_top),
    ]
    all_results: Dict[str, Tuple[float, Dict[str, Any]]] = {}
    for name, fn in detectors:
        try:
            conf, meta = fn(candles)
            if conf > 0:
                all_results[name] = (conf, meta)
        except Exception:
            continue

    if not all_results:
        return {
            "pattern": None, "direction": "none", "confidence": 0,
            "meta": {}, "all_patterns": {},
        }

    best_name, (best_conf, best_meta) = max(
        all_results.items(), key=lambda kv: kv[1][0]
    )
    if best_conf < min_confidence:
        return {
            "pattern": None, "direction": "none",
            "confidence": round(best_conf, 1),
            "meta": best_meta,
            "all_patterns": {n: round(c, 1) for n, (c, _) in all_results.items()},
        }

    return {
        "pattern": best_name,
        "direction": _PATTERN_DIR.get(best_name, "none"),
        "confidence": round(best_conf, 1),
        "meta": best_meta,
        "all_patterns": {n: round(c, 1) for n, (c, _) in all_results.items()},
    }
