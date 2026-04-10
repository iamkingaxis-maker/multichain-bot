"""
Real-Time WebSocket Price Feed
Replaces slow polling with live price streams.

Solana: Helius WebSocket — account subscriptions on token pools
Base/BNB: DexScreener WebSocket + fallback polling at 3s intervals

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

# WebSocket endpoints
DEXSCREENER_WS = "wss://io.dexscreener.com/dex/screener/v7/pairs/h24/1"
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
        self._ws_tick_count = 0
        self.ws_connected = False          # True while DexScreener WS is live
        self.ws_consecutive_failures = 0   # reset on each successful connect
        self._active_ws = None             # live WebSocket object for mid-session subscriptions

        # AxiomPriceFeed-compatible caches — populated on every tick so that
        # position_manager._update_price() can read them with the same staleness logic.
        self.price_cache: Dict[str, float] = {}
        self.price_timestamps: Dict[str, float] = {}
        self.volume_cache: Dict[str, float] = {}
        self.liquidity_cache: Dict[str, float] = {}

        # Set by caller so we can fire check_stop_loss_realtime on every tick.
        self.position_manager = None

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
        # Push subscription to live WebSocket immediately so new positions get
        # real-time ticks instead of waiting for the next poll cycle.
        if is_new and self._active_ws is not None:
            import asyncio
            sub_msg = json.dumps({"type": "subscribe", "payload": {"tokenAddresses": [addr]}})
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self._active_ws.send_str(sub_msg))
            except Exception:
                pass  # polling fallback still covers it

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
        """Start the price feed — runs WebSocket + polling fallback concurrently."""
        self._running = True
        logger.info("[PriceFeed] Starting real-time price feed...")

        await asyncio.gather(
            self._run_dexscreener_ws(),
            self._run_polling_fallback(),
            self._run_stats_logger()
        )

    def _get_ws_url(self) -> str:
        """
        Return the WebSocket URL to use for DexScreener.
        Prefers the Cloudflare Worker proxy (bypasses Railway IP block).
        Falls back to direct connection if proxy env vars are not set.
        """
        relay_url    = os.environ.get("AXIOM_REFRESH_RELAY_URL", "")
        relay_secret = os.environ.get("AXIOM_REFRESH_RELAY_SECRET", "")
        if relay_url and relay_secret:
            worker_base = relay_url.replace("/refresh", "").rstrip("/")
            ws_base     = worker_base.replace("https://", "wss://").replace("http://", "ws://")
            return f"{ws_base}/ds-proxy?s={relay_secret}"
        return DEXSCREENER_WS

    async def _run_dexscreener_ws(self):
        """
        Connect to DexScreener WebSocket for real-time pair updates.
        Tries direct connection first; falls back to Cloudflare Worker proxy if blocked.
        Polling fallback always runs concurrently at 1s intervals as a safety net.
        Does not give up — keeps retrying with exponential backoff (max 60s).
        """
        consecutive_failures = 0
        ws_gave_up = False
        while self._running:
            # Try direct first; only use proxy after direct fails with 403
            if consecutive_failures == 0 or not ws_gave_up:
                ws_url = DEXSCREENER_WS
                via = "direct"
            else:
                ws_url = self._get_ws_url()
                via = "proxy" if "ds-proxy" in ws_url else "direct"

            try:
                logger.info(f"[PriceFeed] Connecting to DexScreener WebSocket ({via})...")
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(
                        ws_url,
                        heartbeat=30,
                        timeout=aiohttp.ClientWSTimeout(ws_close=60)
                    ) as ws:
                        logger.info(f"[PriceFeed] DexScreener WebSocket connected ({via})")
                        consecutive_failures = 0
                        self.ws_consecutive_failures = 0
                        self.ws_connected = True
                        self._active_ws = ws
                        ws_gave_up = False

                        # Subscribe to watched tokens
                        await self._send_dexscreener_subscriptions(ws)

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle_dexscreener_message(msg.data)
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                logger.warning("[PriceFeed] WS error — reconnecting")
                                break
                            elif msg.type == aiohttp.WSMsgType.CLOSED:
                                break

                        self._active_ws = None

            except asyncio.CancelledError:
                self._active_ws = None
                break
            except Exception as e:
                self._active_ws = None
                consecutive_failures += 1
                self.ws_consecutive_failures = consecutive_failures
                self.ws_connected = False
                backoff = min(5 * consecutive_failures, 60)
                # Escalate to ERROR after 10 failures — this is a broken endpoint, not a blip
                log_fn = logger.error if consecutive_failures >= 10 else logger.info
                log_fn(
                    f"[PriceFeed] DexScreener WS failed ({via}, attempt {consecutive_failures}): "
                    f"{type(e).__name__} — polling covers stops, retry in {backoff}s"
                    + (" *** PERSISTENT FAILURE — endpoint may have changed ***" if consecutive_failures == 10 else "")
                )
                if consecutive_failures == 3 and via == "direct":
                    # Switch to proxy path for subsequent retries
                    ws_gave_up = True
                await asyncio.sleep(backoff)
                continue
            finally:
                self.ws_connected = False

            await asyncio.sleep(5)

    async def _send_dexscreener_subscriptions(self, ws):
        """Send subscription messages for all watched tokens."""
        if not self._watched:
            return
        sub_msg = {
            "type": "subscribe",
            "payload": {
                "tokenAddresses": list(self._watched)
            }
        }
        await ws.send_str(json.dumps(sub_msg))

    async def _handle_dexscreener_message(self, raw: str):
        """Parse incoming DexScreener WebSocket message."""
        try:
            data = json.loads(raw)
            msg_type = data.get("type", "")

            if msg_type == "pairs":
                pairs = data.get("pairs", [])
                for pair in pairs:
                    await self._process_pair_update(pair, source="websocket")

            elif msg_type == "pair":
                await self._process_pair_update(data.get("pair", {}), source="websocket")

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.debug(f"[PriceFeed] Message parse error: {e}")

    async def _process_pair_update(self, pair: dict, source: str = "poll"):
        """Convert a pair update into a PriceTick and notify subscribers."""
        try:
            base_token = pair.get("baseToken", {})
            token_address = base_token.get("address", "").lower()
            chain_id = pair.get("chainId", "")

            if token_address not in self._watched:
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
            if source == "websocket":
                self._ws_tick_count += 1

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
        Runs at 1-second intervals when tokens are being watched (position open).
        Uses 3-second intervals when nothing is watched to avoid unnecessary calls.
        """
        while self._running:
            if not self._watched:
                await asyncio.sleep(3)
                continue

            tokens_to_poll = list(self._watched)
            # Batch into groups of 30 (DexScreener limit)
            for i in range(0, len(tokens_to_poll), 30):
                batch = tokens_to_poll[i:i+30]
                await self._poll_batch(batch)
                if len(tokens_to_poll) > 30:
                    await asyncio.sleep(0.5)

            # 1s cycle when watching positions — stop-loss latency matters
            await asyncio.sleep(1)

    async def _poll_batch(self, addresses: list):
        """Poll a batch of token addresses from DexScreener."""
        try:
            joined = ",".join(addresses)
            url = f"https://api.dexscreener.com/latest/dex/tokens/{joined}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
                    pairs = data.get("pairs", [])

                    # Keep only the highest-liquidity pair per token
                    seen = set()
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
            ws_pct = (
                self._ws_tick_count / self._tick_count * 100
                if self._tick_count > 0 else 0
            )
            logger.info(
                f"[PriceFeed] Stats — Ticks: {self._tick_count} | "
                f"WebSocket: {ws_pct:.1f}% | "
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
        ws_pct = (
            self._ws_tick_count / self._tick_count * 100
            if self._tick_count > 0 else 0
        )
        return {
            "total_ticks": self._tick_count,
            "websocket_ticks": self._ws_tick_count,
            "websocket_pct": ws_pct,
            "watching": len(self._watched),
            "subscribers": sum(len(v) for v in self._subscribers.values())
        }
