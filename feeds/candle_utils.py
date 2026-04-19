"""Pure candle math — no I/O, no side effects. Reusable by detector + tests."""
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int


def ema(values: List[float], period: int) -> float:
    """Standard exponential moving average. Returns simple mean if series shorter than period."""
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    alpha = 2.0 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = alpha * v + (1 - alpha) * e
    return e


def rolling_avg_volume(candles: List[Candle], n: int) -> float:
    """Mean volume of the last n candles (or all if fewer)."""
    if not candles:
        return 0.0
    tail = candles[-n:]
    return sum(c.volume for c in tail) / len(tail)


def consecutive_reds_no_wick(candles: List[Candle], n: int) -> bool:
    """
    True if the last n candles are all red AND have no lower wick
    (low == min(open, close)). Used by SOL regime guard.
    """
    if len(candles) < n:
        return False
    for c in candles[-n:]:
        if c.close >= c.open:
            return False
        if c.low < min(c.open, c.close) - 1e-12:
            return False
    return True
