"""
Risk Manager
Controls position sizing, daily loss limits, and overall capital protection.
"""

import json
import logging
import os
from datetime import datetime, timezone, date

logger = logging.getLogger(__name__)

_DATA_DIR = os.environ.get("DATA_DIR", ".")
_RISK_STATE_FILE = os.path.join(_DATA_DIR, "risk_state.json")


class RiskManager:
    def __init__(self, total_capital: float, max_position_pct: float, daily_loss_limit: float,
                 max_position_usd: float = 0.0):
        self.total_capital = total_capital
        self.available_capital = total_capital
        self.max_position_pct = max_position_pct
        self.max_position_usd = max_position_usd  # hard cap — prevents compounding runaway
        self.daily_loss_limit = daily_loss_limit

        self.daily_pnl = 0.0
        self.daily_date = date.today()
        self.total_pnl = 0.0
        self.trades_today = 0
        self.config_min_copy_sol = 0.5  # Minimum SOL size to copy

        # Restore available_capital from disk so restarts don't over-allocate
        self._load_state()

    def _load_state(self):
        """Restore available_capital from disk (survives restarts).

        On restart, open positions from the previous session are NOT restored
        (they only live in memory). Any deployed_capital saved in the state file
        represents positions that are now gone, so we return it to available.
        This prevents capital from leaking on every redeploy.

        One-time migration: old state files have no deployed_capital field.
        On first run with this code, reset to total_capital (abandoned positions
        from previous sessions are treated as liquidated at cost, P&L = 0).
        """
        try:
            if os.path.exists(_RISK_STATE_FILE):
                with open(_RISK_STATE_FILE) as f:
                    state = json.load(f)

                if "deployed_capital" not in state:
                    # Old format — one-time migration: return all capital
                    self.available_capital = self.total_capital
                    logger.info(
                        f"[RiskManager] Capital migration: reset to ${self.total_capital:.0f} "
                        f"(old state had no position tracking; abandoned positions cleared)"
                    )
                    self._save_state()
                    return

                saved = float(state.get("available_capital", self.total_capital))
                # Deployed capital = positions that were open at shutdown.
                # Since positions are not persisted, they're gone — return that capital.
                deployed = float(state.get("deployed_capital", 0.0))
                restored = min(saved + deployed, self.total_capital)
                self.available_capital = restored
                logger.info(
                    f"[RiskManager] Restored available_capital: ${self.available_capital:.0f} "
                    f"(total: ${self.total_capital:.0f}, reclaimed deployed: ${deployed:.0f})"
                )
        except Exception as e:
            logger.warning(f"[RiskManager] Could not load risk state: {e}")

    def _save_state(self):
        """Persist available_capital to disk after every buy/sell.

        Uses atomic write (tmp file + os.replace) so a SIGKILL during
        Railway redeploy can't truncate the file. A truncated risk_state
        causes _load_state to fall back to total_capital on next boot,
        forgetting deployed positions and allowing capital double-count.
        """
        try:
            tmp_path = _RISK_STATE_FILE + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump({
                    "available_capital": self.available_capital,
                    "deployed_capital": self.total_capital - self.available_capital,
                }, f)
            os.replace(tmp_path, _RISK_STATE_FILE)
        except Exception as e:
            logger.warning(f"[RiskManager] Could not save risk state: {e}")

    def reconcile_with_open_positions(self, open_positions: dict) -> None:
        """
        Recompute available_capital from the canonical source of truth: the
        actual open positions loaded on startup.

        Why this is needed: the legacy `_load_state` behavior reclaims any
        previously-deployed capital on restart, assuming positions don't
        survive a redeploy. That assumption was correct for paper mode in
        the original bot, but live-mode persistence (open_positions.json)
        means positions DO survive — and reclaiming their cost basis double-
        counts capital. With max_concurrent=3 × $20 + a real $20 open
        position, the bot would think it has $70 free against a $70 wallet
        when it actually has $50 free.

        Idempotent — call after `_restore_open_positions` and again after
        `reconcile_positions_on_startup` (which may remove ghosts).
        Scalp positions are excluded — they manage their own capital pool
        via ScalpCapitalManager.
        """
        try:
            deployed = sum(
                float(getattr(p, "amount_usd", 0) or 0)
                for p in open_positions.values()
                if (getattr(p, "strategy", "") or "") != "scalp"
            )
            new_available = max(0.0, self.total_capital - deployed)
            if abs(new_available - self.available_capital) >= 0.01:
                logger.info(
                    f"[RiskManager] Reconciled with {len(open_positions)} open "
                    f"positions: deployed=${deployed:.0f}, "
                    f"available ${self.available_capital:.0f} → ${new_available:.0f}"
                )
            self.available_capital = new_available
            self._save_state()
        except Exception as e:
            logger.warning(f"[RiskManager] reconcile_with_open_positions failed: {e}")

    def get_position_size(self) -> float:
        """Calculate safe position size in USD."""
        self._reset_daily_if_needed()

        if self.is_daily_limit_hit():
            logger.warning("🛑 Daily loss limit reached — no new trades today")
            return 0

        max_position = self.available_capital * self.max_position_pct
        # Never risk more than available capital
        position = min(max_position, self.available_capital * 0.10)
        # Hard cap — prevents position sizes from compounding as paper capital grows
        if self.max_position_usd > 0:
            position = min(position, self.max_position_usd)
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
        self._save_state()
        logger.info(f"📊 Capital remaining: ${self.available_capital:.0f}")

    def record_sell(self, usd_received: float, pnl: float):
        """Record a sell and update capital and PnL."""
        self.available_capital += usd_received
        self.daily_pnl += pnl
        self.total_pnl += pnl
        self._save_state()
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
