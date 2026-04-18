"""
BreakoutCapitalManager — independent $2000 capital pool for the breakout strategy.

Completely separate from RiskManager and ScalpCapitalManager. Tracks deployed
capital, concurrent position count, and cumulative realized P&L. No daily loss
limit — risk is managed per-position (3% stop) and by max_concurrent cap.
"""

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class BreakoutCapitalManager:
    total_capital: float = 2000.0
    max_concurrent: int = 4

    _deployed: Dict[str, float] = field(default_factory=dict, init=False)
    _realized_pnl: float = field(default=0.0, init=False)

    def has_capacity(self, position_usd: float) -> bool:
        if len(self._deployed) >= self.max_concurrent:
            return False
        return self.available_usd() >= position_usd

    def reserve(self, symbol: str, position_usd: float) -> None:
        self._deployed[symbol] = position_usd

    def release(self, symbol: str, proceeds_usd: float, cost_usd: float) -> None:
        if symbol not in self._deployed:
            return
        del self._deployed[symbol]
        self._realized_pnl += proceeds_usd - cost_usd

    def available_usd(self) -> float:
        return self.total_capital + self._realized_pnl - self.deployed_usd()

    def deployed_usd(self) -> float:
        return sum(self._deployed.values())

    def realized_pnl(self) -> float:
        return self._realized_pnl

    def stats(self) -> dict:
        return {
            "total_capital": self.total_capital,
            "available": self.available_usd(),
            "deployed": self.deployed_usd(),
            "open_count": len(self._deployed),
            "max_concurrent": self.max_concurrent,
            "realized_pnl": self._realized_pnl,
        }
