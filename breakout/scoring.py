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
