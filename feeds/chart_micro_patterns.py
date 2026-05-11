"""Micro chart pattern detection on 1m bars — D1 ext / Path 2 (2026-05-11).

Sibling to feeds/chart_patterns.py (which runs on 5m bars via chart_reader).
This module detects fast-resolution patterns on the trailing 1m series
and emits a COMPOSITE bullish/bearish score plus individual flags.

Existing chart_patterns.py emits one named pattern at a time on 5m
bars. This module is complementary:
  - Operates on 1m bars (higher resolution)
  - Detects MULTIPLE concurrent patterns
  - Sums weighted detections into chart_micro_pattern_score (-100..+100)
  - Bullish positive, bearish negative
  - Includes price-action singles (engulfing, wicks, inside bar)

Patterns:
  REVERSAL: double_top, double_bottom, head_shoulders,
            inverse_head_shoulders, rising_wedge, falling_wedge,
            bearish_engulfing, bullish_engulfing
  CONTINUATION: bull_flag, bear_flag, ascending_triangle,
                descending_triangle
  PRICE-ACTION: long_upper_wick, long_lower_wick, inside_bar

Forward-only: persisted in entry_meta on every dip_buy. Validate
against outcomes after ~30-50 closed trades, then potentially gate.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple


def _find_pivot_highs(highs: Sequence[float], pivot_n: int = 2) -> List[int]:
    out: List[int] = []
    if len(highs) < 2 * pivot_n + 1: return out
    for i in range(pivot_n, len(highs) - pivot_n):
        h = highs[i]
        if all(highs[j] < h for j in range(i - pivot_n, i)) and \
           all(highs[j] < h for j in range(i + 1, i + pivot_n + 1)):
            out.append(i)
    return out


def _find_pivot_lows(lows: Sequence[float], pivot_n: int = 2) -> List[int]:
    out: List[int] = []
    if len(lows) < 2 * pivot_n + 1: return out
    for i in range(pivot_n, len(lows) - pivot_n):
        ll = lows[i]
        if all(lows[j] > ll for j in range(i - pivot_n, i)) and \
           all(lows[j] > ll for j in range(i + 1, i + pivot_n + 1)):
            out.append(i)
    return out


def _approx_eq(a: float, b: float, tol_pct: float = 0.02) -> bool:
    if a == 0 or b == 0: return False
    return abs(a - b) / max(abs(a), abs(b)) <= tol_pct


def _linreg_slope(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3 or n != len(ys): return None
    sx = sum(xs); sy = sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxx = sum(x * x for x in xs)
    denom = n * sxx - sx * sx
    if denom == 0: return None
    return (n * sxy - sx * sy) / denom


# ─── REVERSAL ───────────────────────────────────────────────────

def _double_top(candles: Sequence[Any], lookback: int = 30) -> bool:
    if not candles or len(candles) < lookback: return False
    cs = candles[-lookback:]
    highs = [c.high for c in cs]
    pivots = _find_pivot_highs(highs, pivot_n=2)
    if len(pivots) < 2: return False
    p = pivots[-2:]
    h1, h2 = highs[p[0]], highs[p[1]]
    if not _approx_eq(h1, h2, 0.02): return False
    between = highs[p[0]+1:p[1]]
    if not between or min(between) >= min(h1, h2) * 0.99: return False
    return p[1] >= len(cs) - lookback / 3


def _double_bottom(candles: Sequence[Any], lookback: int = 30) -> bool:
    if not candles or len(candles) < lookback: return False
    cs = candles[-lookback:]
    lows = [c.low for c in cs]
    pivots = _find_pivot_lows(lows, pivot_n=2)
    if len(pivots) < 2: return False
    p = pivots[-2:]
    l1, l2 = lows[p[0]], lows[p[1]]
    if not _approx_eq(l1, l2, 0.02): return False
    between = lows[p[0]+1:p[1]]
    if not between or max(between) <= max(l1, l2) * 1.01: return False
    return p[1] >= len(cs) - lookback / 3


def _head_shoulders(candles: Sequence[Any], lookback: int = 40) -> bool:
    if not candles or len(candles) < lookback: return False
    cs = candles[-lookback:]
    highs = [c.high for c in cs]
    pivots = _find_pivot_highs(highs, pivot_n=2)
    if len(pivots) < 3: return False
    p = pivots[-3:]
    ls, head, rs = highs[p[0]], highs[p[1]], highs[p[2]]
    return (head > ls and head > rs
            and _approx_eq(ls, rs, 0.05)
            and head >= max(ls, rs) * 1.02)


def _inv_head_shoulders(candles: Sequence[Any], lookback: int = 40) -> bool:
    if not candles or len(candles) < lookback: return False
    cs = candles[-lookback:]
    lows = [c.low for c in cs]
    pivots = _find_pivot_lows(lows, pivot_n=2)
    if len(pivots) < 3: return False
    p = pivots[-3:]
    ls, head, rs = lows[p[0]], lows[p[1]], lows[p[2]]
    return (head < ls and head < rs
            and _approx_eq(ls, rs, 0.05)
            and head <= min(ls, rs) * 0.98)


def _rising_wedge(candles: Sequence[Any], lookback: int = 30) -> bool:
    """Both highs+lows rising, lows faster (converging upward → bearish)."""
    if not candles or len(candles) < lookback: return False
    cs = candles[-lookback:]
    highs = [c.high for c in cs]; lows = [c.low for c in cs]
    ph = _find_pivot_highs(highs, pivot_n=2)
    pl = _find_pivot_lows(lows, pivot_n=2)
    if len(ph) < 2 or len(pl) < 2: return False
    hs = _linreg_slope(ph, [highs[i] for i in ph])
    ls = _linreg_slope(pl, [lows[i] for i in pl])
    if hs is None or ls is None: return False
    return hs > 0 and ls > 0 and ls > hs


def _falling_wedge(candles: Sequence[Any], lookback: int = 30) -> bool:
    """Both falling, highs faster (converging downward → bullish)."""
    if not candles or len(candles) < lookback: return False
    cs = candles[-lookback:]
    highs = [c.high for c in cs]; lows = [c.low for c in cs]
    ph = _find_pivot_highs(highs, pivot_n=2)
    pl = _find_pivot_lows(lows, pivot_n=2)
    if len(ph) < 2 or len(pl) < 2: return False
    hs = _linreg_slope(ph, [highs[i] for i in ph])
    ls = _linreg_slope(pl, [lows[i] for i in pl])
    if hs is None or ls is None: return False
    return hs < 0 and ls < 0 and hs < ls


def _bearish_engulfing(candles: Sequence[Any]) -> bool:
    if not candles or len(candles) < 2: return False
    p, c = candles[-2], candles[-1]
    return (p.close > p.open  # prev green
            and c.close < c.open  # curr red
            and c.open >= p.close
            and c.close <= p.open)


def _bullish_engulfing(candles: Sequence[Any]) -> bool:
    if not candles or len(candles) < 2: return False
    p, c = candles[-2], candles[-1]
    return (p.close < p.open
            and c.close > c.open
            and c.open <= p.close
            and c.close >= p.open)


# ─── CONTINUATION ───────────────────────────────────────────────

def _bull_flag(candles: Sequence[Any], lookback: int = 25) -> bool:
    if not candles or len(candles) < lookback: return False
    cs = candles[-lookback:]
    third = max(5, len(cs) // 3)
    pole, flag = cs[:third], cs[third:]
    if not pole or not flag: return False
    if pole[0].close <= 0: return False
    pole_chg = (pole[-1].close / pole[0].close - 1) * 100
    if pole_chg < 5: return False
    if flag[0].close <= 0: return False
    flag_chg = (flag[-1].close / flag[0].close - 1) * 100
    if not (-5 < flag_chg < 1): return False
    if max(c.high for c in flag) > max(c.high for c in pole): return False
    return True


def _bear_flag(candles: Sequence[Any], lookback: int = 25) -> bool:
    if not candles or len(candles) < lookback: return False
    cs = candles[-lookback:]
    third = max(5, len(cs) // 3)
    pole, flag = cs[:third], cs[third:]
    if not pole or not flag: return False
    if pole[0].close <= 0: return False
    pole_chg = (pole[-1].close / pole[0].close - 1) * 100
    if pole_chg > -5: return False
    if flag[0].close <= 0: return False
    flag_chg = (flag[-1].close / flag[0].close - 1) * 100
    if not (-1 < flag_chg < 5): return False
    if min(c.low for c in flag) < min(c.low for c in pole): return False
    return True


def _asc_triangle(candles: Sequence[Any], lookback: int = 30) -> bool:
    if not candles or len(candles) < lookback: return False
    cs = candles[-lookback:]
    highs = [c.high for c in cs]; lows = [c.low for c in cs]
    ph = _find_pivot_highs(highs, pivot_n=2)
    pl = _find_pivot_lows(lows, pivot_n=2)
    if len(ph) < 2 or len(pl) < 2: return False
    h_vals = [highs[i] for i in ph]
    if (max(h_vals) - min(h_vals)) / max(h_vals) > 0.02: return False
    ls = _linreg_slope(pl, [lows[i] for i in pl])
    return ls is not None and ls > 0


def _desc_triangle(candles: Sequence[Any], lookback: int = 30) -> bool:
    if not candles or len(candles) < lookback: return False
    cs = candles[-lookback:]
    highs = [c.high for c in cs]; lows = [c.low for c in cs]
    ph = _find_pivot_highs(highs, pivot_n=2)
    pl = _find_pivot_lows(lows, pivot_n=2)
    if len(ph) < 2 or len(pl) < 2: return False
    l_vals = [lows[i] for i in pl]
    if (max(l_vals) - min(l_vals)) / max(l_vals) > 0.02: return False
    hs = _linreg_slope(ph, [highs[i] for i in ph])
    return hs is not None and hs < 0


# ─── PRICE-ACTION SINGLES ────────────────────────────────────────

def _long_upper_wick(candles: Sequence[Any], ratio: float = 2.0) -> bool:
    if not candles: return False
    c = candles[-1]
    body = abs(c.close - c.open)
    upper = c.high - max(c.open, c.close)
    if body == 0: return upper > 0
    return upper >= ratio * body and upper > 0


def _long_lower_wick(candles: Sequence[Any], ratio: float = 2.0) -> bool:
    if not candles: return False
    c = candles[-1]
    body = abs(c.close - c.open)
    lower = min(c.open, c.close) - c.low
    if body == 0: return lower > 0
    return lower >= ratio * body and lower > 0


def _inside_bar(candles: Sequence[Any]) -> bool:
    if not candles or len(candles) < 2: return False
    p, c = candles[-2], candles[-1]
    return c.high <= p.high and c.low >= p.low


# Pattern → weight. Bullish positive, bearish negative.
PATTERN_WEIGHTS: Dict[str, int] = {
    "double_bottom": +25,
    "double_top": -25,
    "inverse_head_shoulders": +30,
    "head_shoulders": -30,
    "falling_wedge": +20,
    "rising_wedge": -20,
    "bullish_engulfing": +15,
    "bearish_engulfing": -15,
    "bull_flag": +20,
    "bear_flag": -20,
    "ascending_triangle": +15,
    "descending_triangle": -15,
    "long_lower_wick": +10,
    "long_upper_wick": -10,
    "inside_bar": 0,
}

_DETECTORS = {
    "double_top": _double_top,
    "double_bottom": _double_bottom,
    "head_shoulders": _head_shoulders,
    "inverse_head_shoulders": _inv_head_shoulders,
    "rising_wedge": _rising_wedge,
    "falling_wedge": _falling_wedge,
    "bearish_engulfing": _bearish_engulfing,
    "bullish_engulfing": _bullish_engulfing,
    "bull_flag": _bull_flag,
    "bear_flag": _bear_flag,
    "ascending_triangle": _asc_triangle,
    "descending_triangle": _desc_triangle,
    "long_upper_wick": _long_upper_wick,
    "long_lower_wick": _long_lower_wick,
    "inside_bar": _inside_bar,
}


def compute_micro_patterns(candles_1m: Sequence[Any]) -> Dict[str, Any]:
    """Detect named micro patterns on 1m series + composite score.

    Returns dict:
      micro_pattern_<name>: bool for each detector
      micro_pattern_score:  int [-100, +100] sum of weights of detected
      micro_pattern_detected: list of detected pattern names
    """
    if not candles_1m:
        return {}
    out: Dict[str, Any] = {}
    detected: List[str] = []
    score = 0
    for name, det in _DETECTORS.items():
        try:
            fired = bool(det(candles_1m))
        except Exception:
            fired = False
        out[f"micro_pattern_{name}"] = fired
        if fired:
            detected.append(name)
            score += PATTERN_WEIGHTS.get(name, 0)
    out["micro_pattern_score"] = max(-100, min(100, score))
    out["micro_pattern_detected"] = detected
    return out
