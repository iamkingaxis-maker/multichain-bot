"""
Telegram Alpha Channel Monitor

Listens to a list of caller/alpha channels and extracts token contract
addresses from messages. Each discovered address is injected directly
into the scanner's full evaluation pipeline (score → security → dip check).

Requires Railway Variables:
  TELEGRAM_API_ID            — numeric ID from my.telegram.org
  TELEGRAM_API_HASH          — hash string from my.telegram.org
  TELEGRAM_SESSION           — StringSession from scripts/gen_session.py
  TELEGRAM_MONITOR_CHANNELS  — comma-separated channel usernames (no @)

Generate TELEGRAM_SESSION by running:
  python scripts/gen_session.py
"""

import asyncio
import logging
import re
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Solana base58 address: 32-44 chars, base58 alphabet (no 0, O, I, l)
_SOL_RE = re.compile(r'\b([1-9A-HJ-NP-Za-km-z]{32,44})\b')

# EVM address: 0x + 40 hex chars
_EVM_RE = re.compile(r'\b(0x[0-9a-fA-F]{40})\b')

# Well-known program/system addresses that are NOT tokens
_SKIP_SOL = {
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1brs",
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s",
    "TokenzQdBNbequnAA3bPoNqKkSE6dE7Ub2JdEyMQDE",
}

# Don't re-evaluate the same address within this window
_DEDUP_SECONDS = 600  # 10 minutes


class TelegramChannelMonitor:
    """
    Monitors Telegram channels for token contract addresses.
    Feeds discovered addresses into the scanner evaluation pipeline.
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_string: str,
        channels: List[str],
        sol_scanner=None,
        base_scanner=None,
        bnb_scanner=None,
    ):
        self.api_id         = api_id
        self.api_hash       = api_hash
        self.session_string = session_string
        # Strip @ prefix if present
        self.channels = [c.lstrip("@").strip() for c in channels if c.strip()]
        self.sol_scanner  = sol_scanner
        self.base_scanner = base_scanner
        self.bnb_scanner  = bnb_scanner
        self._seen: Dict[str, float] = {}   # addr_lower → last_seen monotonic
        self._messages_processed = 0
        self._addresses_injected = 0

    async def run(self):
        if not self.api_id or not self.api_hash or not self.session_string:
            logger.warning(
                "[TelegramMonitor] Missing credentials "
                "(TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_SESSION) — disabled"
            )
            return

        if not self.channels:
            logger.warning("[TelegramMonitor] No channels configured — disabled")
            return

        try:
            from pyrogram import Client
            from pyrogram.handlers import MessageHandler
            from pyrogram.types import Message
        except ImportError:
            logger.error(
                "[TelegramMonitor] pyrogram not installed — "
                "add 'pyrogram' and 'tgcrypto' to requirements.txt"
            )
            return

        client = Client(
            name="tg_alpha_monitor",
            api_id=self.api_id,
            api_hash=self.api_hash,
            session_string=self.session_string,
            no_updates=False,
        )

        async def handle_message(c, message: Message):
            # Only process messages from our monitored channels
            chat = message.chat
            username = getattr(chat, "username", None) or ""
            if username.lower() not in {ch.lower() for ch in self.channels}:
                return
            text = message.text or message.caption or ""
            if not text:
                return
            self._messages_processed += 1
            await self._process_message(text, username)

        async with client:
            client.add_handler(MessageHandler(handle_message))
            logger.info(
                f"[TelegramMonitor] Connected — watching "
                + ", ".join(f"@{c}" for c in self.channels)
            )
            await asyncio.sleep(float("inf"))

    async def _process_message(self, text: str, channel: str):
        now = time.monotonic()
        # Expire stale dedup entries
        self._seen = {a: t for a, t in self._seen.items()
                      if now - t < _DEDUP_SECONDS}

        found = []

        # Solana addresses
        if self.sol_scanner:
            for m in _SOL_RE.finditer(text):
                addr = m.group(1)
                if addr in _SKIP_SOL:
                    continue
                key = addr.lower()
                if key in self._seen:
                    continue
                self._seen[key] = now
                found.append(("solana", addr))
                asyncio.create_task(self._inject_solana(addr, channel))

        # EVM addresses (Base + BNB)
        if self.base_scanner or self.bnb_scanner:
            for m in _EVM_RE.finditer(text):
                addr = m.group(1).lower()
                if addr in self._seen:
                    continue
                self._seen[addr] = now
                found.append(("evm", addr))
                asyncio.create_task(self._inject_evm(addr, channel))

        if found:
            logger.info(
                f"[TelegramMonitor] @{channel}: "
                f"{len(found)} address(es) → "
                + ", ".join(f"{chain}:{addr[:8]}…" for chain, addr in found)
            )

    async def _inject_solana(self, address: str, source: str):
        try:
            await self.sol_scanner.inject_token_from_address(address, source)
            self._addresses_injected += 1
        except Exception as e:
            logger.debug(f"[TelegramMonitor] Solana inject error {address[:8]}: {e}")

    async def _inject_evm(self, address: str, source: str):
        for scanner, name in [(self.base_scanner, "Base"), (self.bnb_scanner, "BNB")]:
            if not scanner:
                continue
            try:
                await scanner.inject_token_from_address(address, source)
                self._addresses_injected += 1
                break
            except Exception as e:
                logger.debug(f"[TelegramMonitor] {name} inject error {address[:8]}: {e}")

    def get_stats(self) -> dict:
        return {
            "messages_processed": self._messages_processed,
            "addresses_injected": self._addresses_injected,
            "channels_watched":   len(self.channels),
            "dedup_cache_size":   len(self._seen),
        }
