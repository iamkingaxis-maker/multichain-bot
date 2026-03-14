"""
Risk Manager
Controls position sizing, daily loss limits, and overall capital protection.
"""

import logging
from datetime import datetime, timezone, date

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, total_capital: float, max_position_pct: float, daily_loss_limit: float):
        self.total_capital = total_capital
        self.available_capital = total_capital
        self.max_position_pct = max_position_pct
        self.daily_loss_limit = daily_loss_limit

        self.daily_pnl = 0.0
        self.daily_date = date.today()
        self.total_pnl = 0.0
        self.trades_today = 0
        self.config_min_copy_sol = 0.5  # Minimum SOL size to copy

    def get_position_size(self) -> float:
        """Calculate safe position size in USD."""
        self._reset_daily_if_needed()

        if self.is_daily_limit_hit():
            logger.warning("🛑 Daily loss limit reached — no new trades today")
            return 0

        max_position = self.available_capital * self.max_position_pct
        # Never risk more than available capital
        position = min(max_position, self.available_capital * 0.10)
        logger.info(f"💰 Position size: ${position:.0f} ({self.max_position_pct*100:.0f}% of ${self.available_capital:.0f})")
        return position

    def is_daily_limit_hit(self) -> bool:
        """Check if daily loss limit has been reached."""
        self._reset_daily_if_needed()
        return self.daily_pnl <= -self.daily_loss_limit

    def record_buy(self, usd_spent: float):
        """Record a buy and reduce available capital."""
        self.available_capital -= usd_spent
        self.trades_today += 1
        logger.info(f"📊 Capital remaining: ${self.available_capital:.0f}")

    def record_sell(self, usd_received: float, pnl: float):
        """Record a sell and update capital and PnL."""
        self.available_capital += usd_received
        self.daily_pnl += pnl
        self.total_pnl += pnl
        logger.info(
            f"📊 PnL today: ${self.daily_pnl:+.0f} | "
            f"Total: ${self.total_pnl:+.0f} | "
            f"Capital: ${self.available_capital:.0f}"
        )

    def get_summary(self) -> dict:
        """Return a summary of current risk state."""
        self._reset_daily_if_needed()
        return {
            "total_capital": self.total_capital,
            "available_capital": self.available_capital,
            "daily_pnl": self.daily_pnl,
            "total_pnl": self.total_pnl,
            "trades_today": self.trades_today,
            "daily_limit_hit": self.is_daily_limit_hit(),
            "daily_limit_remaining": self.daily_loss_limit + self.daily_pnl
        }

    def get_dashboard_stats(self) -> dict:
        """Return stats in format expected by web dashboard."""
        s = self.get_summary()
        deployed = s["total_capital"] - s["available_capital"]
        return {
            "capital": {
                "total": s["total_capital"],
                "available": s["available_capital"],
                "deployed": deployed,
            },
            "daily_pnl": s["daily_pnl"],
        }

    def _reset_daily_if_needed(self):
        """Reset daily stats at midnight."""
        today = date.today()
        if today != self.daily_date:
            logger.info(f"🌅 New day — resetting daily PnL (yesterday: ${self.daily_pnl:+.0f})")
            self.daily_pnl = 0.0
            self.trades_today = 0
            self.daily_date = today
