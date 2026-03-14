"""
Performance Tracker & Dashboard
Tracks Scanner, Copy Trader, and Scalper trades separately.
"""

import asyncio
import logging
import json
import os
from datetime import datetime, timezone
from typing import Dict, List

logger = logging.getLogger(__name__)
TRADE_LOG_FILE = "trades.json"


class PerformanceTracker:
    def __init__(self):
        self.trades: List[dict] = []
        self.scalpers = []          # registered scalper instances
        self._load_trades()

    def register_scalper(self, scalper):
        """Register a scalper so the dashboard can show its stats."""
        self.scalpers.append(scalper)

    def record_buy(self, position):
        trade = {
            "type": "buy",
            "strategy": self._detect_strategy(getattr(position, "reason", "")),
            "chain": getattr(position, "chain_id", "unknown"),
            "token": getattr(position, "token_symbol", "?"),
            "address": getattr(position, "token_address", ""),
            "entry_price": getattr(position, "entry_price_usd", 0),
            "amount_usd": getattr(position, "entry_usd_value",
                          getattr(position, "amount_sol_spent", 0)),
            "time": datetime.now(timezone.utc).isoformat(),
            "reason": getattr(position, "reason", "")
        }
        self.trades.append(trade)
        self._save_trades()

    def record_sell(self, token_address: str, usd_received: float,
                    pnl: float, reason: str):
        trade = {
            "type": "sell",
            "strategy": self._detect_strategy(reason),
            "address": token_address,
            "usd_received": usd_received,
            "pnl": pnl,
            "time": datetime.now(timezone.utc).isoformat(),
            "reason": reason
        }
        self.trades.append(trade)
        self._save_trades()

    def _detect_strategy(self, reason: str) -> str:
        r = reason.upper()
        if "SCALP" in r:
            return "scalper"
        elif "COPY" in r:
            return "copy"
        else:
            return "scanner"

    def get_stats(self, strategy: str = None) -> dict:
        sells = [t for t in self.trades if t["type"] == "sell"]
        if strategy:
            sells = [t for t in sells if t.get("strategy") == strategy]
        if not sells:
            return {"total_trades": 0, "win_rate": 0, "total_pnl": 0,
                    "avg_win": 0, "avg_loss": 0, "best_trade": 0, "worst_trade": 0}
        wins = [t for t in sells if t["pnl"] > 0]
        losses = [t for t in sells if t["pnl"] <= 0]
        return {
            "total_trades": len(sells),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(sells) * 100,
            "total_pnl": sum(t["pnl"] for t in sells),
            "avg_win": sum(t["pnl"] for t in wins) / len(wins) if wins else 0,
            "avg_loss": sum(t["pnl"] for t in losses) / len(losses) if losses else 0,
            "best_trade": max((t["pnl"] for t in sells), default=0),
            "worst_trade": min((t["pnl"] for t in sells), default=0)
        }

    async def run_dashboard(self):
        while True:
            await asyncio.sleep(300)
            self._print_dashboard()

    def _print_dashboard(self):
        overall = self.get_stats()
        scanner = self.get_stats("scanner")
        copy = self.get_stats("copy")
        scalper = self.get_stats("scalper")

        print("\n" + "="*56)
        print("  📊 MULTI-CHAIN BOT v2 — PERFORMANCE DASHBOARD")
        print("="*56)

        print(f"\n  {'OVERALL':}")
        print(f"  Total Trades:  {overall['total_trades']} | "
              f"Win Rate: {overall['win_rate']:.1f}% | "
              f"PnL: ${overall['total_pnl']:+,.2f}")

        print(f"\n  {'SCANNER'}")
        print(f"  Trades: {scanner['total_trades']} | "
              f"Win Rate: {scanner['win_rate']:.1f}% | "
              f"PnL: ${scanner['total_pnl']:+,.2f}")

        print(f"\n  {'COPY TRADER'}")
        print(f"  Trades: {copy['total_trades']} | "
              f"Win Rate: {copy['win_rate']:.1f}% | "
              f"PnL: ${copy['total_pnl']:+,.2f}")

        print(f"\n  {'SCALPER'}")
        print(f"  Trades: {scalper['total_trades']} | "
              f"Win Rate: {scalper['win_rate']:.1f}% | "
              f"PnL: ${scalper['total_pnl']:+,.2f}")

        # Live scalper state per chain
        if self.scalpers:
            print(f"\n  {'LIVE SCALPER STATE'}")
            for s in self.scalpers:
                stats = s.get_stats()
                print(f"  [{stats['chain']}] Capital: "
                      f"${stats['available_capital']:.0f} | "
                      f"Active: {stats['active_scalps']} | "
                      f"Today: ${stats['daily_pnl']:+.2f}")

        # Recent trades
        recent = [t for t in self.trades if t["type"] == "sell"][-6:]
        if recent:
            print(f"\n  {'RECENT TRADES'}")
            for t in reversed(recent):
                pnl = t.get("pnl", 0)
                emoji = "🟢" if pnl > 0 else "🔴"
                strat = t.get("strategy", "?")[:4].upper()
                print(f"  {emoji} [{strat}] PnL: ${pnl:+.2f} | {t.get('reason','')[:35]}")

        print("="*56 + "\n")

    def _save_trades(self):
        try:
            with open(TRADE_LOG_FILE, "w") as f:
                json.dump(self.trades, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save trades: {e}")

    def _load_trades(self):
        if os.path.exists(TRADE_LOG_FILE):
            try:
                with open(TRADE_LOG_FILE, "r") as f:
                    self.trades = json.load(f)
                logger.info(f"Loaded {len(self.trades)} historical trades")
            except Exception:
                self.trades = []

    def get_dashboard_stats(self) -> dict:
        """Return stats in format expected by web dashboard."""
        overall = self.get_stats()
        scanner = self.get_stats("scanner")
        copy = self.get_stats("copy")
        scalper = self.get_stats("scalper")

        recent = [
            t for t in self.trades
            if t["type"] == "sell"
        ][-10:]

        return {
            "overall": overall,
            "strategies": {
                "scanner": scanner,
                "copy": copy,
                "scalper": scalper
            },
            "recent_trades": list(reversed(recent))
        }
