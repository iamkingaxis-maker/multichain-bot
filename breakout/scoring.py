"""
Pure-function scoring primitives for the breakout strategy.

All functions are stateless, deterministic, unit-testable in isolation.
No network, no DB, no logging.
"""

from dataclasses import dataclass


@dataclass
class Kline:
    """Binance klines row, strongly typed."""
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int


def ema(prices: list[float], period: int) -> float:
    """
    EMA of the last-N prices, seeded by SMA of the first `period` values.
    If `prices` is shorter than `period`, falls back to the simple mean.
    """
    if not prices:
        raise ValueError("ema() requires at least one price")
    n = len(prices)
    if n < period:
        return sum(prices) / n
    alpha = 2.0 / (period + 1)
    seed = sum(prices[:period]) / period
    value = seed
    for p in prices[period:]:
        value = value + alpha * (p - value)
    return value


def breakout_strength_score(
    *,
    candle: Kline,
    avg_volume_20: float,
    resistance: float,
    ema50_1h: float,
    ema200_1h: float,
    consolidation_range: float,
) -> tuple[int, dict]:
    """
    Returns (total_score 0-10, breakdown dict for logging).

    Per spec:
      - Volume expansion (0-3):  >=1.5x avg → +3, >=1.2x → +2, >=1.0x → +1
      - Candle body (0-2):       body_ratio > 0.7 → +2, > 0.5 → +1
      - Breakout size (0-2):     > 0.5% → +2, > 0.2% → +1
      - Trend sep (0-2):         > 1% above ema50 → +2, > 0 → +1
      - Clean structure (0-1):   consolidation_range/resistance < 1% → +1

    All gates are independent; final sum clipped at [0, 10].
    """
    if avg_volume_20 > 0:
        vol_ratio = candle.volume / avg_volume_20
    else:
        vol_ratio = 0.0
    if vol_ratio >= 1.5:
        vol_score = 3
    elif vol_ratio >= 1.2:
        vol_score = 2
    elif vol_ratio >= 1.0:
        vol_score = 1
    else:
        vol_score = 0

    candle_range = candle.high - candle.low
    if candle_range > 0:
        body_ratio = abs(candle.close - candle.open) / candle_range
    else:
        body_ratio = 0.0
    if body_ratio > 0.7:
        body_score = 2
    elif body_ratio > 0.5:
        body_score = 1
    else:
        body_score = 0

    if resistance > 0 and candle.close > resistance:
        breakout_pct = (candle.close - resistance) / resistance
    else:
        breakout_pct = 0.0
    if breakout_pct > 0.005:
        break_score = 2
    elif breakout_pct > 0.002:
        break_score = 1
    else:
        break_score = 0

    if ema50_1h > 0 and candle.close > ema50_1h and ema50_1h > ema200_1h:
        trend_sep = (candle.close - ema50_1h) / ema50_1h
        if trend_sep > 0.01:
            trend_score = 2
        elif trend_sep > 0:
            trend_score = 1
        else:
            trend_score = 0
    else:
        trend_score = 0

    if resistance > 0:
        struct_ratio = consolidation_range / resistance
    else:
        struct_ratio = 1.0
    struct_score = 1 if struct_ratio < 0.01 else 0

    total = vol_score + body_score + break_score + trend_score + struct_score
    total = max(0, min(10, total))
    breakdown = {
        "volume": vol_score,
        "body": body_score,
        "breakout_size": break_score,
        "trend": trend_score,
        "structure": struct_score,
        "total": total,
    }
    return total, breakdown


def is_bearish_engulfing(prev: Kline, curr: Kline) -> bool:
    """Prev green, curr red, curr body engulfs prev body."""
    prev_green = prev.close > prev.open
    curr_red = curr.close < curr.open
    if not (prev_green and curr_red):
        return False
    return curr.open >= prev.close and curr.close <= prev.open


def has_upper_wick_rejection(candle: Kline, threshold: float = 0.6) -> bool:
    """Upper wick > `threshold` of total range signals rejection."""
    r = candle.high - candle.low
    if r <= 0:
        return False
    body_top = max(candle.open, candle.close)
    upper_wick = candle.high - body_top
    return (upper_wick / r) > threshold


def volume_drop(current_vol: float, baseline_vol: float, threshold: float = 0.5) -> bool:
    """current_vol < threshold * baseline_vol."""
    if baseline_vol <= 0:
        return False
    return current_vol < threshold * baseline_vol
