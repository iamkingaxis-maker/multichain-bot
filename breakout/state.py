"""
BreakoutState — in-memory shared container.

Holds the live watchlist (set by scanner, read by strategy),
open positions (written by execution), last-seen candle close times
(strategy uses for edge detection), and a rolling counter dict
(diagnostic logging).
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class BreakoutPosition:
    symbol: str
    entry_time: str
    entry_price: float
    qty: float
    cost_usd: float
    score: int
    resistance_level: float
    tp_price: float
    stop_price: float
    entry_candle_volume: float
    peak_price: float
    tp_hit: bool = False
    score_breakdown: dict = field(default_factory=dict)
    reason_entry: str = ""


@dataclass
class BreakoutState:
    watchlist: List[str] = field(default_factory=list)
    open_positions: Dict[str, BreakoutPosition] = field(default_factory=dict)
    last_seen_close: Dict[str, int] = field(default_factory=dict)
    scan_counters: Dict[str, int] = field(default_factory=dict)

    def set_watchlist(self, symbols: list[str]) -> None:
        self.watchlist = list(symbols)

    def bump(self, key: str) -> None:
        self.scan_counters[key] = self.scan_counters.get(key, 0) + 1

    def reset_scan_counters(self) -> None:
        self.scan_counters = {}
