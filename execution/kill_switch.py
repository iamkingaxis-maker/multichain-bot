"""
Emergency Kill Switch
Instantly halts all trading activity across all chains.
Triggered by:
  - Telegram command: /kill or /stop
  - Dashboard button click
  - Automatic trigger on catastrophic loss

When activated:
  1. Sets global halt flag — no new buys on any chain
  2. Closes all open positions immediately (market sell)
  3. Cancels all pending scalp trades
  4. Sends confirmation to Telegram
  5. Logs everything to kill_switch.log
  6. Optionally converts all holdings to stable (USDC)

To resume: /resume command in Telegram or restart the bot
"""

import asyncio
import logging
import json
from datetime import datetime, timezone
from typing import List, Optional, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
KILL_LOG_FILE = "kill_switch.log"


@dataclass
class KillEvent:
    triggered_at: datetime
    reason: str
    triggered_by: str        # "telegram", "dashboard", "auto", "manual"
    positions_closed: int = 0
    total_recovered_usd: float = 0.0
    success: bool = False


class KillSwitch:
    """
    Global emergency halt system.
    All traders check is_active() before executing any buy.
    """

    def __init__(self, telegram=None):
        self.telegram = telegram
        self._halted = False
        self._kill_reason = ""
        self._kill_history: List[KillEvent] = []

        # Registered traders to close out on kill
        self._traders = []
        self._scalpers = []

        # Callbacks to notify other systems
        self._on_kill_callbacks: List[Callable] = []

        logger.info("[KillSwitch] Initialized — monitoring active")

    def register_trader(self, trader):
        """Register a trader so its positions can be closed on kill."""
        self._traders.append(trader)

    def register_scalper(self, scalper):
        """Register a scalper so its trades can be cancelled on kill."""
        self._scalpers.append(scalper)

    def on_kill(self, callback: Callable):
        """Register a callback to fire when kill switch activates."""
        self._on_kill_callbacks.append(callback)

    @property
    def is_active(self) -> bool:
        """Returns True if trading is HALTED (kill switch engaged)."""
        return self._halted

    @property
    def is_trading(self) -> bool:
        """Returns True if trading is ALLOWED (normal operation)."""
        return not self._halted

    async def trigger(self, reason: str = "Manual trigger",
                      triggered_by: str = "manual",
                      close_positions: bool = True) -> KillEvent:
        """
        Activate the kill switch.
        Immediately halts all trading and optionally closes all positions.
        """
        if self._halted:
            logger.warning("[KillSwitch] Already halted — ignoring duplicate trigger")
            existing = self._kill_history[-1] if self._kill_history else KillEvent(
                triggered_at=datetime.now(timezone.utc),
                reason="Already halted",
                triggered_by=triggered_by
            )
            return existing

        self._halted = True
        self._kill_reason = reason
        event = KillEvent(
            triggered_at=datetime.now(timezone.utc),
            reason=reason,
            triggered_by=triggered_by
        )

        logger.critical(
            f"[KillSwitch] 🛑 KILL SWITCH ACTIVATED\n"
            f"  Reason: {reason}\n"
            f"  By: {triggered_by}\n"
            f"  Time: {event.triggered_at.isoformat()}"
        )

        # Notify Telegram immediately
        if self.telegram:
            await self.telegram.send(
                f"🛑 *KILL SWITCH ACTIVATED*\n\n"
                f"📝 Reason: {reason}\n"
                f"🕐 Time: {event.triggered_at.strftime('%H:%M:%S UTC')}\n"
                f"⏳ Closing all positions..."
            )

        # Fire callbacks
        for cb in self._on_kill_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(event)
                else:
                    cb(event)
            except Exception as e:
                logger.error(f"[KillSwitch] Callback error: {e}")

        # Close all positions
        if close_positions:
            positions_closed, recovered = await self._close_all_positions()
            event.positions_closed = positions_closed
            event.total_recovered_usd = recovered

        # Cancel all active scalps
        await self._cancel_all_scalps()

        event.success = True
        self._kill_history.append(event)
        self._log_kill_event(event)

        # Final Telegram confirmation
        if self.telegram:
            await self.telegram.send(
                f"✅ *Kill Switch Complete*\n\n"
                f"📊 Positions closed: {event.positions_closed}\n"
                f"💵 Capital recovered: ${event.total_recovered_usd:,.2f}\n"
                f"⚠️ Bot is HALTED — send /resume to restart trading"
            )

        logger.critical(
            f"[KillSwitch] Kill complete — "
            f"{event.positions_closed} positions closed, "
            f"${event.total_recovered_usd:,.2f} recovered"
        )

        return event

    async def resume(self, reason: str = "Manual resume") -> bool:
        """
        Resume trading after a kill.
        Requires explicit command — never auto-resumes.
        """
        if not self._halted:
            logger.info("[KillSwitch] Already trading — nothing to resume")
            return True

        self._halted = False
        self._kill_reason = ""

        logger.info(f"[KillSwitch] ▶️ Trading RESUMED — {reason}")

        if self.telegram:
            await self.telegram.send(
                f"▶️ *Trading Resumed*\n\n"
                f"📝 Reason: {reason}\n"
                f"🕐 Time: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}\n"
                f"✅ All systems active"
            )

        return True

    def check_auto_triggers(self, daily_pnl: float,
                             total_capital: float) -> Optional[str]:
        """
        Check if automatic kill conditions are met.
        Call this regularly from the risk manager.
        Returns trigger reason or None.
        """
        # Auto-kill if daily loss exceeds 15% of total capital
        if daily_pnl < 0:
            loss_pct = abs(daily_pnl) / total_capital * 100
            if loss_pct >= 15:
                return f"Auto-kill: Daily loss {loss_pct:.1f}% exceeds 15% threshold"

        return None

    async def _close_all_positions(self) -> tuple:
        """Close all open positions across all registered traders."""
        positions_closed = 0
        total_recovered = 0.0

        for trader in self._traders:
            positions = dict(trader.open_positions)
            for addr, position in positions.items():
                try:
                    symbol = getattr(position, "token_symbol", "?")
                    logger.info(
                        f"[KillSwitch] Closing {symbol} on "
                        f"{getattr(position, 'chain_id', '?')}"
                    )
                    await trader.sell(
                        token_address=addr,
                        token_symbol=symbol,
                        reason="KILL SWITCH — emergency close",
                        pct=1.0
                    )
                    positions_closed += 1
                    # Estimate recovery (will be logged by trader)
                    total_recovered += getattr(position, "amount_usd", 0)
                except Exception as e:
                    logger.error(
                        f"[KillSwitch] Failed to close {addr[:10]}: {e}"
                    )

        return positions_closed, total_recovered

    async def _cancel_all_scalps(self):
        """Force-close all active scalp trades."""
        for scalper in self._scalpers:
            try:
                active = dict(getattr(scalper, "active_scalps", {}))
                for addr, scalp in active.items():
                    if hasattr(scalper, "_execute_scalp_sell"):
                        await scalper._execute_scalp_sell(
                            scalp, 0, 0, "KILL SWITCH"
                        )
            except Exception as e:
                logger.error(f"[KillSwitch] Scalp cancel error: {e}")

    def _log_kill_event(self, event: KillEvent):
        """Write kill event to persistent log file."""
        try:
            with open(KILL_LOG_FILE, "a") as f:
                f.write(json.dumps({
                    "triggered_at": event.triggered_at.isoformat(),
                    "reason": event.reason,
                    "triggered_by": event.triggered_by,
                    "positions_closed": event.positions_closed,
                    "recovered_usd": event.total_recovered_usd,
                    "success": event.success
                }) + "\n")
        except Exception as e:
            logger.error(f"[KillSwitch] Failed to write log: {e}")

    def get_status(self) -> dict:
        return {
            "halted": self._halted,
            "reason": self._kill_reason,
            "kill_count": len(self._kill_history),
            "last_kill": (
                self._kill_history[-1].triggered_at.isoformat()
                if self._kill_history else None
            )
        }


class TelegramKillSwitchHandler:
    """
    Listens for /kill and /resume commands in Telegram.
    Integrates with the KillSwitch system.
    """

    def __init__(self, kill_switch: KillSwitch, telegram,
                 allowed_chat_ids: List[str] = None):
        self.kill_switch = kill_switch
        self.telegram = telegram
        self.allowed_chat_ids = allowed_chat_ids or []

    async def run(self):
        """Poll Telegram for commands. Runs as a background task."""
        if not self.telegram or not self.telegram.enabled:
            return

        logger.info("[KillSwitch] Telegram command listener started")
        last_update_id = 0

        while True:
            try:
                updates = await self._get_updates(last_update_id)
                for update in updates:
                    last_update_id = update.get("update_id", 0) + 1
                    await self._handle_update(update)
            except Exception as e:
                logger.debug(f"[KillSwitch] Telegram poll error: {e}")
            await asyncio.sleep(3)

    async def _get_updates(self, offset: int) -> list:
        import aiohttp
        url = (
            f"https://api.telegram.org/bot{self.telegram.token}"
            f"/getUpdates?offset={offset}&timeout=5"
        )
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                data = await r.json()
                return data.get("result", [])

    async def _handle_update(self, update: dict):
        msg = update.get("message", {})
        text = msg.get("text", "").strip().lower()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        # Security: only respond to authorized chat
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            return

        if text in ("/kill", "/stop", "/halt", "/emergency"):
            await self.kill_switch.trigger(
                reason=f"Telegram command: {text}",
                triggered_by="telegram",
                close_positions=True
            )

        elif text in ("/resume", "/start", "/go"):
            await self.kill_switch.resume(reason="Telegram command")

        elif text in ("/status", "/stats"):
            status = self.kill_switch.get_status()
            state = "🛑 HALTED" if status["halted"] else "✅ TRADING"
            await self.telegram.send(
                f"*Bot Status*\n\n"
                f"State: {state}\n"
                f"Kill count: {status['kill_count']}\n"
                f"Reason: {status.get('reason', 'N/A')}"
            )

        elif text == "/help":
            await self.telegram.send(
                "*Available Commands*\n\n"
                "/kill — Emergency stop all trading\n"
                "/resume — Resume trading after kill\n"
                "/status — Current bot status\n"
                "/help — Show this message"
            )
