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
import time
from typing import Dict, Callable, Optional, Set
from datetime import datetime, timezone
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# WebSocket endpoints
DEXSCREENER_WS = "wss://io.dexscreener.com/dex/screener/pairs/h24/1"
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
    """

    def __init__(self, helius_api_key: str = ""):
        self.helius_api_key = helius_api_key
        self._subscribers: Dict[str, list] = {}   # token_address -> [callbacks]
        self._latest: Dict[str, PriceTick] = {}   # token_address -> latest tick
        self._watched: Set[str] = set()            # tokens currently watched
        self._watch_chains: Dict[str, str] = {}    # token_address -> chain_id
        self._poll_interval: Dict[str, float] = {} # token_address -> poll interval
        self._running = False
        self._tick_count = 0
        self._ws_tick_count = 0

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

    async def _run_dexscreener_ws(self):
        """
        Connect to DexScreener WebSocket for real-time pair updates.
        Reconnects automatically on disconnect.
        """
        while self._running:
            try:
                logger.info("[PriceFeed] Connecting to DexScreener WebSocket...")
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(
                        DEXSCREENER_WS,
                        heartbeat=30,
                        timeout=aiohttp.ClientWSTimeout(ws_close=60)
                    ) as ws:
                        logger.info("[PriceFeed] DexScreener WebSocket connected")

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

            except asyncio.CancelledError:
                break
            except Exception as e:
                err_str = str(e)
                if "403" in err_str:
                    logger.warning("[PriceFeed] DexScreener WebSocket blocked (403) — using polling only")
                    return  # Don't retry — polling fallback handles it
                logger.error(f"[PriceFeed] DexScreener WS error: {e}")

            logger.info("[PriceFeed] WebSocket disconnected — reconnecting in 5s...")
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
        Runs at 3-second intervals per token — much faster than the old 10s.
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
                await asyncio.sleep(0.5)

            await asyncio.sleep(3)

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

        except Exception as e:
            logger.debug(f"[PriceFeed] Poll batch error: {e}")

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
