"""
Real-Time Price Feed (HTTP polling + Helius/Axiom WS for stops).

Solana: HTTP polling at 0.5-3s + Axiom WS on watched positions.
DexScreener public price WS has been deprecated by DS (the v7 endpoint
the bot relied on now returns 404, and v5 — discovered via the
dexscraper library 2026-05-12 — uses a binary snapshot-then-close
model behind Cloudflare bypass that delivers no advantage over our
HTTP polling). Removed 2026-05-12.

Provides a unified price feed interface regardless of chain.
The scalper subscribes to this feed for near-instant dip detection.
"""

import asyncio
import logging
import aiohttp
import json
import os
import time
from typing import Dict, Callable, Optional, Set
from datetime import datetime, timezone
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

HELIUS_WS_BASE = "wss://mainnet.helius-rpc.com/?api-key="


@dataclass
class PriceTick:
    """A single real-time price update."""
    token_address: str
    chain_id: str
    price_usd: float
    volume_usd: float
    price_change_pct: float    # vs previous tick
    liquidity_usd: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "unknown"    # "websocket" or "poll"


class PriceFeed:
    """
    Unified real-time price feed for all chains.
    Subscribers register callbacks that fire on every price tick.

    Also exposes an AxiomPriceFeed-compatible interface so position_manager
    can use it as a drop-in replacement for the Axiom cache:
      price_cache[token_address]      → latest price in USD
      price_timestamps[token_address] → unix timestamp of last update
      subscribe_token(addr)           → start watching
      unsubscribe_token(addr)         → stop watching
    """

    def __init__(self, helius_api_key: str = ""):
        self.helius_api_key = helius_api_key
        self._subscribers: Dict[str, list] = {}   # token_address -> [callbacks]
        self._latest: Dict[str, PriceTick] = {}   # token_address -> latest tick
        self._watched: Set[str] = set()            # tokens currently watched
        self._watch_chains: Dict[str, str] = {}    # token_address -> chain_id
        self._poll_interval: Dict[str, float] = {} # token_address -> poll interval
        self._pair_addresses: Dict[str, str] = {}  # token_address -> pair_address (for direct pair lookup)
        self._running = False
        self._tick_count = 0

        # AxiomPriceFeed-compatible caches — populated on every tick so that
        # position_manager._update_price() can read them with the same staleness logic.
        self.price_cache: Dict[str, float] = {}
        self.price_timestamps: Dict[str, float] = {}
        self.volume_cache: Dict[str, float] = {}
        self.liquidity_cache: Dict[str, float] = {}

        # Set by caller so we can fire check_stop_loss_realtime on every tick.
        self.position_manager = None

        # DexScreener 429 backoff: when banned, sleep this long before next poll.
        # Prevents 2-RPS retry storm + log spam while IP cools down. Helius/Axiom
        # feeds carry Solana stops during the window; only cross-chain stops degrade.
        self._ds_backoff_until: float = 0.0

    # ── AxiomPriceFeed-compatible subscription API ──────────────────────────

    def subscribe_token(self, token_address: str, chain_id: str = "solana", pair_address: str = ""):
        """Subscribe to price updates for a token (AxiomPriceFeed-compatible API)."""
        addr = token_address.lower()
        is_new = addr not in self._watched
        if is_new:
            self._watched.add(addr)
            self._watch_chains[addr] = chain_id
            logger.debug(f"[PriceFeed] Subscribed: {addr[:8]}…")
        if pair_address:
            self._pair_addresses[addr] = pair_address
        # New positions are picked up by the next poll cycle (≤500ms when
        # there are watched tokens, ≤3s when idle). Axiom WS handles
        # sub-second updates on subscribed positions.

    def unsubscribe_token(self, token_address: str):
        """Stop watching a token and clear its caches (AxiomPriceFeed-compatible API)."""
        addr = token_address.lower()
        self._watched.discard(addr)
        self._subscribers.pop(addr, None)
        self._watch_chains.pop(addr, None)
        self._poll_interval.pop(addr, None)
        self._pair_addresses.pop(addr, None)
        self.price_cache.pop(addr, None)
        self.price_timestamps.pop(addr, None)
        self.volume_cache.pop(addr, None)
        self.liquidity_cache.pop(addr, None)
        logger.debug(f"[PriceFeed] Unsubscribed: {addr[:8]}…")

    def subscribe(self, token_address: str, chain_id: str,
                  callback: Callable[[PriceTick], None],
                  poll_interval_seconds: float = 3.0):
        """
        Subscribe to price updates for a token.
        callback(tick) is called on every price update.
        """
        addr = token_address.lower()
        if addr not in self._subscribers:
            self._subscribers[addr] = []
        self._subscribers[addr].append(callback)
        self._watched.add(addr)
        self._watch_chains[addr] = chain_id
        self._poll_interval[addr] = poll_interval_seconds
        logger.debug(f"[PriceFeed] Subscribed to {addr[:10]}... on {chain_id}")

    def unsubscribe(self, token_address: str):
        """Stop watching a token."""
        addr = token_address.lower()
        self._watched.discard(addr)
        self._subscribers.pop(addr, None)
        self._watch_chains.pop(addr, None)
        self._poll_interval.pop(addr, None)
        logger.debug(f"[PriceFeed] Unsubscribed from {addr[:10]}...")

    def get_latest(self, token_address: str) -> Optional[PriceTick]:
        return self._latest.get(token_address.lower())

    async def run(self):
        """Start the price feed — HTTP polling + stats logger."""
        self._running = True
        logger.info("[PriceFeed] Starting real-time price feed...")

        await asyncio.gather(
            self._run_polling_fallback(),
            self._run_stats_logger()
        )

    async def _process_pair_update(self, pair: dict, source: str = "poll"):
        """Convert a pair update into a PriceTick and notify subscribers."""
        try:
            base_token = pair.get("baseToken", {})
            token_address = base_token.get("address", "").lower()
            chain_id = pair.get("chainId", "")

            if token_address not in self._watched:
                return

            # Pair-pinning gate 2026-05-07 PM: multi-pair tokens (e.g.
            # PENGUIN had a raydium pair priced at $0.163 vs pumpswap pair
            # at $0.004 — 37x apart) were causing apples-to-oranges price
            # comparisons because both pairs ticked through and the
            # higher-liq pair won. Bot bought on pair X, but current_price
            # got overwritten by pair Y. When a pair_address is pinned for
            # this token via subscribe_token(...), only accept ticks
            # whose pairAddress matches. Fail-open if not pinned.
            pinned_pair = self._pair_addresses.get(token_address)
            if pinned_pair:
                incoming_pair = (pair.get("pairAddress") or "").lower()
                if incoming_pair and incoming_pair != pinned_pair.lower():
                    return

            price_str = pair.get("priceUsd", "0")
            price = float(price_str) if price_str else 0
            if price <= 0:
                return

            volume = pair.get("volume", {}).get("h1", 0)
            liquidity = pair.get("liquidity", {}).get("usd", 0)
            price_change = pair.get("priceChange", {}).get("m5", 0) or 0

            tick = PriceTick(
                token_address=token_address,
                chain_id=chain_id,
                price_usd=price,
                volume_usd=volume,
                price_change_pct=float(price_change),
                liquidity_usd=liquidity,
                source=source
            )

            prev = self._latest.get(token_address)
            if prev and prev.price_usd > 0:
                tick.price_change_pct = ((price - prev.price_usd) / prev.price_usd) * 100

            self._latest[token_address] = tick
            self._tick_count += 1

            # Populate AxiomPriceFeed-compatible caches
            self.price_cache[token_address] = price
            self.price_timestamps[token_address] = time.time()
            if volume:
                self.volume_cache[token_address] = volume
            if liquidity:
                self.liquidity_cache[token_address] = liquidity

            # Fire realtime stop-loss check — same pattern as AxiomPriceFeed
            if self.position_manager is not None:
                self.position_manager.check_stop_loss_realtime(token_address, price)
                self.position_manager.check_take_profit_realtime(token_address, price)
                self.position_manager.check_exhaustion_realtime(token_address, price)
                self.position_manager.check_post_tp1_trail_realtime(token_address, price)

            # Notify all subscribers
            callbacks = self._subscribers.get(token_address, [])
            for callback in callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(tick)
                    else:
                        callback(tick)
                except Exception as e:
                    logger.debug(f"[PriceFeed] Callback error: {e}")

        except Exception as e:
            logger.debug(f"[PriceFeed] Pair update error: {e}")

    async def _run_polling_fallback(self):
        """
        Poll DexScreener REST API for tokens not covered by WebSocket.
        Runs at 500ms intervals when tokens are being watched (position open) —
        this is the only realtime path for dip_buy positions (Axiom WS covers scalps).
        Uses 3-second intervals when nothing is watched to avoid unnecessary calls.
        """
        while self._running:
            if not self._watched:
                await asyncio.sleep(3)
                continue

            # Honor 429 backoff window — DS rate-limits sometimes return 429 on
            # every request for 30+ seconds. Without this, we'd burn 60 req/s of
            # log spam and request budget retrying through the ban.
            now = time.time()
            if now < self._ds_backoff_until:
                await asyncio.sleep(min(5.0, self._ds_backoff_until - now))
                continue

            tokens_to_poll = list(self._watched)
            # Batch into groups of 30 (DexScreener limit)
            for i in range(0, len(tokens_to_poll), 30):
                batch = tokens_to_poll[i:i+30]
                await self._poll_batch(batch)
                if len(tokens_to_poll) > 30:
                    await asyncio.sleep(0.5)

            await asyncio.sleep(0.5)

    async def _poll_batch(self, addresses: list):
        """Poll a batch of token addresses from DexScreener."""
        try:
            joined = ",".join(addresses)
            url = f"https://api.dexscreener.com/latest/dex/tokens/{joined}"
            # Real-browser UA — DS appears to UA-fingerprint headless callers
            # and throttle them harder than browser-style requests.
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status != 200:
                        if resp.status == 429:
                            # Enter 30s backoff. Only log when first entering the
                            # window (suppresses thousands of duplicate WARNINGs
                            # during a ban). Helius/Axiom price feeds still
                            # cover Solana stops during the cooldown.
                            already_in_backoff = time.time() < self._ds_backoff_until
                            self._ds_backoff_until = time.time() + 30.0
                            if not already_in_backoff:
                                logger.warning(
                                    f"[PriceFeed] DexScreener 429 ({len(addresses)} tokens) — "
                                    f"backing off 30s. Helius/Axiom feeds carry Solana stops."
                                )
                        elif resp.status >= 500:
                            logger.warning(
                                f"[PriceFeed] Poll batch HTTP {resp.status} "
                                f"({len(addresses)} tokens) — stop-loss realtime degraded"
                            )
                        return
                    data = await resp.json()
                    pairs = data.get("pairs", [])

                    # Pair selection 2026-05-07 PM: prefer pinned pair_address
                    # when registered (the pair the bot actually bought on),
                    # otherwise fall back to highest-liquidity pair.
                    # Avoids the PENGUIN bug where raydium pair $489k liq
                    # outranked the pumpswap entry pair $391k liq, despite
                    # being priced 37x differently.
                    seen = set()
                    # First pass: pinned pair_address matches.
                    for pair in pairs:
                        addr = pair.get("baseToken", {}).get("address", "").lower()
                        if addr in seen or addr not in self._watched:
                            continue
                        pinned = (self._pair_addresses.get(addr) or "").lower()
                        if pinned and (pair.get("pairAddress") or "").lower() == pinned:
                            seen.add(addr)
                            await self._process_pair_update(pair, source="poll")
                    # Second pass: highest-liquidity for tokens with no pinned pair.
                    for pair in sorted(
                        pairs,
                        key=lambda p: p.get("liquidity", {}).get("usd", 0),
                        reverse=True
                    ):
                        addr = pair.get("baseToken", {}).get("address", "").lower()
                        if addr not in seen and addr in self._watched:
                            seen.add(addr)
                            await self._process_pair_update(pair, source="poll")

                    # For tokens with known pair addresses that weren't found above,
                    # poll the pair endpoint directly (helps very new tokens not yet indexed)
                    missing = [a for a in addresses if a not in seen and a in self._pair_addresses]
                    for token_addr in missing:
                        pair_addr = self._pair_addresses[token_addr]
                        await self._poll_pair_direct(token_addr, pair_addr)

        except Exception as e:
            logger.debug(f"[PriceFeed] Poll batch error: {e}")

    async def _poll_pair_direct(self, token_address: str, pair_address: str):
        """Poll a specific pair address directly — used for tokens not yet in DexScreener index."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{pair_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
                    pair = data.get("pair") or (data.get("pairs") or [None])[0]
                    if pair:
                        # Override baseToken address to match our tracked token
                        if "baseToken" not in pair:
                            pair["baseToken"] = {}
                        pair["baseToken"]["address"] = token_address
                        await self._process_pair_update(pair, source="poll")
        except Exception as e:
            logger.debug(f"[PriceFeed] Direct pair poll error: {e}")

    async def _run_helius_ws(self, api_key: str, token_address: str):
        """
        Subscribe to Helius WebSocket for Solana account changes.
        Provides fastest possible price updates on Solana.
        """
        if not api_key:
            return

        url = f"{HELIUS_WS_BASE}{api_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(url, heartbeat=30) as ws:
                    # Subscribe to account notifications
                    sub_msg = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "accountSubscribe",
                        "params": [
                            token_address,
                            {"encoding": "jsonParsed", "commitment": "confirmed"}
                        ]
                    }
                    await ws.send_str(json.dumps(sub_msg))

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            # Account changes trigger a price poll
                            if "params" in data:
                                await self._poll_batch([token_address])
        except Exception as e:
            logger.debug(f"[PriceFeed] Helius WS error for {token_address[:10]}: {e}")

    async def _run_stats_logger(self):
        """Log feed statistics every 5 minutes."""
        while self._running:
            await asyncio.sleep(300)
            logger.info(
                f"[PriceFeed] Stats — Ticks: {self._tick_count} | "
                f"Watching: {len(self._watched)} tokens"
            )

    def add_token(self, token_address: str, chain_id: str):
        """Dynamically add a token to the watch list."""
        addr = token_address.lower()
        self._watched.add(addr)
        self._watch_chains[addr] = chain_id

    def remove_token(self, token_address: str):
        """Remove a token from the watch list."""
        self.unsubscribe(token_address)

    def get_stats(self) -> dict:
        return {
            "total_ticks": self._tick_count,
            "watching": len(self._watched),
            "subscribers": sum(len(v) for v in self._subscribers.values())
        }
