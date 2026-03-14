"""
Telegram Notifier
Sends trade alerts and daily summaries to your Telegram.

Setup:
1. Message @BotFather on Telegram to create a bot and get a token
2. Message @userinfobot to get your chat ID
3. Add both to config.json
"""

import asyncio
import logging
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)

        if not self.enabled:
            logger.warning("Telegram not configured — alerts disabled")

    async def send(self, message: str):
        """Send a message to Telegram."""
        if not self.enabled:
            logger.info(f"[TELEGRAM DISABLED] {message[:100]}")
            return

        url = f"{TELEGRAM_API}{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        logger.error(f"Telegram error {resp.status}: {error}")
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

    async def send_daily_summary(self, risk_summary: dict, open_positions: dict):
        """Send a daily performance summary."""
        pnl = risk_summary["daily_pnl"]
        total_pnl = risk_summary["total_pnl"]
        capital = risk_summary["available_capital"]
        trades = risk_summary["trades_today"]

        emoji = "🟢" if pnl >= 0 else "🔴"
        positions_text = ""
        if open_positions:
            positions_text = "\n\n*Open Positions:*\n"
            for addr, pos in open_positions.items():
                multiplier = pos.current_price_usd / pos.entry_price_usd if pos.entry_price_usd > 0 else 1
                positions_text += f"• ${pos.token_symbol}: {multiplier:.2f}x (${pos.pnl_usd:+.0f})\n"

        message = (
            f"{emoji} *Daily Summary*\n\n"
            f"📅 Trades today: {trades}\n"
            f"💰 Today's PnL: ${pnl:+.0f}\n"
            f"📈 Total PnL: ${total_pnl:+.0f}\n"
            f"💵 Available capital: ${capital:,.0f}"
            f"{positions_text}"
        )
        await self.send(message)
