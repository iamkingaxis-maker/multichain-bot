"""Chart-trend features for entry-quality discrimination — D1 (2026-05-11).

Computed from raw 1m bars at entry time. Captures structural chart
information that aggregate snapshots (h1/h6/h24 deltas) and the existing
chart_shape features (max/drawdown/lower-high count) don't see:

  - Linear regression slope over multi-window time series (15m, 30m, 60m)
  - R² fit quality (trending vs choppy)
  - Higher-high pivot count (mirror of lh_count for bull patterns)
  - Distance from MA20 / MA50 in % terms
  - Slope acceleration (recent vs prior slope)

Hypothesis from session investigation: AVA8/ELIEN class (post-pump
dead-cat after major decay) shows characteristic visual signature:
  - Negative multi-window slopes (downtrend)
  - High R² (strong/clean downtrend, not chop)
  - LH count > HH count
  - Price below MA20 AND MA50
  - Slope NOT decelerating (still bleeding)

These features go into entry_meta on every dip_buy. Forward-collected
for ~7-14 days, then retrained chart classifier to see if they unlock
the signal the existing 50-feature set can't capture.

All features fail-open: if insufficient bars, the feature is omitted
from the result dict (caller can check or default-pass).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def _linear_regression(xs: Sequence[float], ys: Sequence[float]) -> Optional[Dict[str, float]]:
    """Pure-Python OLS for 1D linear regression. Returns dict with
    slope, intercept, r_squared. None if data is degenerate."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    sum_x = sum(xs); sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_xx = sum(x * x for x in xs)
    sum_yy = sum(y * y for y in ys)
    denom_slope = n * sum_xx - sum_x * sum_x
    if denom_slope == 0:
        return None
    slope = (n * sum_xy - sum_x * sum_y) / denom_slope
    intercept = (sum_y - slope * sum_x) / n
    # R² = 1 - SS_res / SS_tot
    mean_y = sum_y / n
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot == 0:
        return {"slope": slope, "intercept": intercept, "r_squared": 1.0}
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_sq = 1 - ss_res / ss_tot
    return {"slope": slope, "intercept": intercept, "r_squared": max(0.0, r_sq)}


def _slope_features(candles: Sequence[Any], window_min: int, prefix: str) -> Dict[str, Any]:
    """Compute slope + R² for the trailing window_min bars."""
    if not candles or len(candles) < min(window_min, 5):
        return {}
    cs = list(candles[-window_min:])
    if len(cs) < 5:
        return {}
    # Normalize closes to the first close to make slope unitless (% per bar)
    closes = [c.close for c in cs if c.close > 0]
    if len(closes) < 5:
        return {}
    base = closes[0]
    if base <= 0:
        return {}
    # ys in pct from start, xs = bar index (== minutes for 1m)
    ys = [(c / base - 1.0) * 100 for c in closes]
    xs = list(range(len(ys)))
    result = _linear_regression(xs, ys)
    if not result:
        return {}
    return {
        f"{prefix}_slope_pct_per_min": round(result["slope"], 4),
        f"{prefix}_r_squared": round(result["r_squared"], 3),
        f"{prefix}_bars": len(closes),
    }


def _find_pivot_lows(lows: Sequence[float], pivot_n: int) -> List[int]:
    out: List[int] = []
    if len(lows) < 2 * pivot_n + 1:
        return out
    for i in range(pivot_n, len(lows) - pivot_n):
        ll = lows[i]
        if all(lows[j] > ll for j in range(i - pivot_n, i)) and \
           all(lows[j] > ll for j in range(i + 1, i + pivot_n + 1)):
            out.append(i)
    return out


def _find_pivot_highs(highs: Sequence[float], pivot_n: int) -> List[int]:
    out: List[int] = []
    if len(highs) < 2 * pivot_n + 1:
        return out
    for i in range(pivot_n, len(highs) - pivot_n):
        h = highs[i]
        if all(highs[j] < h for j in range(i - pivot_n, i)) and \
           all(highs[j] < h for j in range(i + 1, i + pivot_n + 1)):
            out.append(i)
    return out


def _consecutive_higher(values: Sequence[float], rising: bool = True) -> int:
    """Count of consecutive higher (or lower) values ending at the
    most recent. Returns 0 if the sequence breaks."""
    if len(values) < 2:
        return 0
    cnt = 0
    for i in range(len(values) - 1, 0, -1):
        if (rising and values[i] > values[i - 1]) or \
           (not rising and values[i] < values[i - 1]):
            cnt += 1
        else:
            break
    return cnt


def _pivot_features(candles: Sequence[Any], window_min: int, prefix: str) -> Dict[str, Any]:
    """Compute HH / LH / HL / LL counts within the trailing window."""
    pivot_n = 2 if window_min <= 30 else (3 if window_min <= 60 else 4)
    if not candles or len(candles) < 2 * pivot_n + 6:
        return {}
    cs = list(candles[-window_min:])
    if len(cs) < 2 * pivot_n + 6:
        return {}
    highs = [c.high for c in cs]
    lows = [c.low for c in cs]
    pivot_h_idx = _find_pivot_highs(highs, pivot_n)
    pivot_l_idx = _find_pivot_lows(lows, pivot_n)
    p_highs = [highs[i] for i in pivot_h_idx]
    p_lows = [lows[i] for i in pivot_l_idx]
    return {
        f"{prefix}_pivot_n": pivot_n,
        f"{prefix}_n_pivot_highs": len(p_highs),
        f"{prefix}_n_pivot_lows": len(p_lows),
        f"{prefix}_consec_hh": _consecutive_higher(p_highs, rising=True),
        f"{prefix}_consec_lh": _consecutive_higher(p_highs, rising=False),
        f"{prefix}_consec_hl": _consecutive_higher(p_lows, rising=True),
        f"{prefix}_consec_ll": _consecutive_higher(p_lows, rising=False),
    }


def _ma_features(candles: Sequence[Any]) -> Dict[str, Any]:
    """Distance from MA20 / MA50 + cross state."""
    if not candles or len(candles) < 21:
        return {}
    closes = [c.close for c in candles if c.close > 0]
    if len(closes) < 21:
        return {}
    entry = closes[-1]
    if entry <= 0:
        return {}
    ma20 = sum(closes[-20:]) / 20
    out: Dict[str, Any] = {
        "trend_ma20_dist_pct": round((entry / ma20 - 1) * 100, 3) if ma20 > 0 else None,
        "trend_above_ma20": entry > ma20,
    }
    if len(closes) >= 51:
        ma50 = sum(closes[-50:]) / 50
        if ma50 > 0:
            out["trend_ma50_dist_pct"] = round((entry / ma50 - 1) * 100, 3)
            out["trend_above_ma50"] = entry > ma50
            # MA20 vs MA50 cross state: golden (20>50) vs death (20<50)
            out["trend_ma20_above_ma50"] = ma20 > ma50
            out["trend_ma_gap_pct"] = round((ma20 / ma50 - 1) * 100, 3)
    return out


def _slope_acceleration(candles: Sequence[Any]) -> Dict[str, Any]:
    """Compare slope of last 15 bars vs prior 15 bars.

    >0 = accelerating (recent slope steeper than prior in same direction)
    <0 = decelerating
    sign change = trend reversal
    """
    if not candles or len(candles) < 30:
        return {}
    cs = list(candles[-30:])
    if len(cs) < 30:
        return {}
    closes = [c.close for c in cs if c.close > 0]
    if len(closes) < 30:
        return {}
    prior = closes[:15]
    recent = closes[15:]
    pb = prior[0]; rb = recent[0]
    if pb <= 0 or rb <= 0:
        return {}
    prior_ys = [(c / pb - 1) * 100 for c in prior]
    recent_ys = [(c / rb - 1) * 100 for c in recent]
    p_reg = _linear_regression(list(range(15)), prior_ys)
    r_reg = _linear_regression(list(range(15)), recent_ys)
    if not p_reg or not r_reg:
        return {}
    return {
        "trend_slope_prior_15m": round(p_reg["slope"], 4),
        "trend_slope_recent_15m": round(r_reg["slope"], 4),
        "trend_slope_accel": round(r_reg["slope"] - p_reg["slope"], 4),
    }


def compute_chart_trend(candles_1m: Sequence[Any]) -> Dict[str, Any]:
    """Top-level entry. Returns the trend-feature dict.

    All features fail-open: if insufficient bars, omit those features.
    """
    out: Dict[str, Any] = {}
    out.update(_slope_features(candles_1m, 15, "trend_15m"))
    out.update(_slope_features(candles_1m, 30, "trend_30m"))
    out.update(_slope_features(candles_1m, 60, "trend_60m"))
    out.update(_pivot_features(candles_1m, 30, "trend_30m"))
    out.update(_pivot_features(candles_1m, 60, "trend_60m"))
    out.update(_ma_features(candles_1m))
    out.update(_slope_acceleration(candles_1m))
    return out
