"""
Axiom Real-Time Price Feed — Phase 4
Subscribes to Axiom's socket8.axiom.trade WebSocket for per-token price
updates on open positions. Routes price data into a shared price_cache dict
that the position manager can poll before falling back to DexScreener.

Usage:
    price_feed = AxiomPriceFeed(auth_manager=auth, trader=sol_trader)
    tasks.append(price_feed.run())

    # When a position opens:
    price_feed.subscribe_token("So11111111111111111111111111111111111111112")

    # Position manager checks:
    current_price = price_feed.price_cache.get(token_address)
"""

import asyncio
import logging
from typing import Optional, Dict, Set

logger = logging.getLogger(__name__)

try:
    from axiomtradeapi import AxiomTradeClient, AxiomTradeWebSocketClient
    AXIOM_AVAILABLE = True
except ImportError:
    AXIOM_AVAILABLE = False


class AxiomPriceFeed:
    """
    Maintains a live WebSocket subscription to socket8.axiom.trade
    for real-time price updates on open positions.

    Exposes `price_cache: {token_address: price_usd}` for the position
    manager to check before falling back to DexScreener polling.
    """

    def __init__(self,
                 auth_manager,
                 position_manager=None,
                 trader=None):

        self.auth             = auth_manager
        self.position_manager = position_manager
        self.trader           = trader

        # Shared price cache — key: token_address, value: latest price in USD
        self.price_cache: Dict[str, float] = {}
        self.volume_cache: Dict[str, float] = {}
        self.liquidity_cache: Dict[str, float] = {}
        self.change_cache: Dict[str, float] = {}

        # Tokens currently subscribed
        self._subscribed: Set[str] = set()

        # Tokens queued for subscription (added before WS connects)
        self._pending_subscribe: Set[str] = set()
        self._pending_unsubscribe: Set[str] = set()

        # Internal state
        self._ws: Optional[AxiomTradeWebSocketClient] = None
        self._running = False
        self._reconnect_delay = 10

        # Stats
        self.price_updates_received = 0

        self.user_cache: Dict[str, int] = {}
        self._user_baseline_window: Dict[str, list] = {}  # rolling window of last 5 readings

    def subscribe_token(self, token_address: str):
        """
        Subscribe to real-time price updates for a token.
        Safe to call before the WebSocket connects — queued and applied on connect.
        """
        if token_address not in self._subscribed:
            self._pending_subscribe.add(token_address)
            logger.debug(f"[AxiomPriceFeed] Queued subscribe: {token_address[:8]}…")

    def unsubscribe_token(self, token_address: str):
        """
        Stop receiving price updates for a token (position closed).
        """
        self._pending_unsubscribe.add(token_address)
        self._subscribed.discard(token_address)
        self.price_cache.pop(token_address, None)
        self.user_cache.pop(token_address, None)
        self._user_baseline_window.pop(token_address, None)
        self.volume_cache.pop(token_address, None)
        self.liquidity_cache.pop(token_address, None)
        self.change_cache.pop(token_address, None)
        logger.debug(f"[AxiomPriceFeed] Unsubscribed: {token_address[:8]}…")

    async def run(self):
        """Main loop — connects to socket8, subscribes tokens, reconnects on drop."""
        if not AXIOM_AVAILABLE:
            logger.warning(
                "[AxiomPriceFeed] axiomtradeapi not available — price feed disabled"
            )
            return

        if not self.auth.has_credentials:
            logger.warning(
                "[AxiomPriceFeed] No Axiom credentials — price feed disabled"
            )
            return

        logger.info("[AxiomPriceFeed] Starting real-time price feed (socket8.axiom.trade)")
        self._running = True

        _backoff = self._reconnect_delay
        _auth_failures = 0
        _MAX_AUTH_FAILURES = 3

        while self._running:
            try:
                await self._connect_and_stream()
                _backoff = self._reconnect_delay
                _auth_failures = 0
            except Exception as e:
                self._ws = None
                err = str(e).lower()
                is_auth = any(k in err for k in ("auth", "401", "403", "token", "login", "credential"))
                if is_auth:
                    _auth_failures += 1
                    if _auth_failures >= _MAX_AUTH_FAILURES:
                        logger.warning(
                            f"[AxiomPriceFeed] Auth failed {_auth_failures} times — "
                            "pausing 30 min. Update tokens in Railway Variables to restore."
                        )
                        await asyncio.sleep(1800)
                        _auth_failures = 0
                        continue
                    logger.warning(
                        f"[AxiomPriceFeed] Auth error ({_auth_failures}/{_MAX_AUTH_FAILURES}) — "
                        f"retrying in 120s: {e}"
                    )
                    await asyncio.sleep(120)
                else:
                    logger.warning(
                        f"[AxiomPriceFeed] Disconnected — reconnecting in {_backoff}s: {e}"
                    )
                    await asyncio.sleep(_backoff)
                    _backoff = min(_backoff * 2, 300)

    async def _connect_and_stream(self):
        """
        Connect to the token-price WebSocket (socket8) and subscribe to all
        currently tracked tokens.
        """
        token_valid = await self.auth.ensure_valid_token()
        if not token_valid:
            raise Exception("Could not obtain valid Axiom token")

        client = self.auth.get_client()
        if not client:
            raise Exception("Could not create AxiomTradeClient")

        # Get the WS client — it connects to socket8 when is_token_price=True
        ws = client.get_websocket_client()
        self._ws = ws

        # Re-subscribe all previously tracked + any pending tokens
        all_tokens = self._subscribed | self._pending_subscribe
        self._pending_subscribe.clear()

        for token_address in all_tokens:
            if token_address in self._pending_unsubscribe:
                self._pending_unsubscribe.discard(token_address)
                continue

            ticker = self.price_cache.get("__ticker_" + token_address, token_address[:8])

            def make_price_callback(addr, sym):
                async def _on_price(price_data: dict):
                    await self._handle_price_update(addr, sym, price_data)
                return _on_price

            await ws.subscribe_token_price(
                token_address, make_price_callback(token_address, ticker)
            )
            self._subscribed.add(token_address)
            logger.debug(f"[AxiomPriceFeed] Subscribed to price for {token_address[:8]}…")
            await self.subscribe_active_users_for_token(ws, token_address, ticker)

        if not all_tokens:
            logger.info(
                "[AxiomPriceFeed] Connected (no tokens subscribed yet — "
                "will subscribe as positions open)"
            )
        else:
            logger.info(
                f"[AxiomPriceFeed] Connected — subscribed to "
                f"{len(self._subscribed)} token(s)"
            )

        # start() blocks until disconnect — uses is_token_price=True internally
        # The WS client knows to use socket8 when subscribe_token_price was called first
        await ws.start()

        raise Exception("Price feed WebSocket closed cleanly")

    async def _handle_price_update(self,
                                    token_address: str,
                                    ticker: str,
                                    price_data: dict):
        """
        Handle a price update from socket8. Updates price_cache and
        optionally updates the trader's open_positions.
        """
        try:
            self.price_updates_received += 1

            # Price data fields vary — try common names
            price_usd = float(
                price_data.get("priceUsd") or
                price_data.get("price_usd") or
                price_data.get("price") or
                price_data.get("usdPrice") or 0
            )

            if price_usd <= 0:
                return

            self.price_cache[token_address] = price_usd

            volume_usd = float(
                price_data.get("volume") or price_data.get("volumeUsd") or
                price_data.get("volume_usd") or 0
            )
            liquidity_usd = float(
                price_data.get("liquidity") or price_data.get("liquidityUsd") or
                price_data.get("liquidity_usd") or 0
            )
            change_pct = float(
                price_data.get("priceChange") or price_data.get("price_change") or
                price_data.get("change") or 0
            )
            if volume_usd > 0:
                self.volume_cache[token_address] = volume_usd
            if liquidity_usd > 0:
                self.liquidity_cache[token_address] = liquidity_usd
            if change_pct != 0:
                self.change_cache[token_address] = change_pct

            logger.debug(
                f"[AxiomPriceFeed] {ticker}: ${price_usd:.8f}"
            )

            # Update trader's open position current_price if available
            if self.trader and hasattr(self.trader, "open_positions"):
                position = self.trader.open_positions.get(token_address)
                if position and hasattr(position, "current_price"):
                    position.current_price = price_usd

        except Exception as e:
            logger.debug(f"[AxiomPriceFeed] Price handler error: {e}")

    async def _handle_user_count_update(self, token_address: str, ticker: str, count: int):
        """Track active user counts. Spike = current >= 3x rolling baseline."""
        history = self._user_baseline_window.setdefault(token_address, [])
        history.append(count)
        if len(history) > 5:
            history.pop(0)
        self.user_cache[token_address] = count

        if len(history) >= 4:
            baseline = sum(history[:-1]) / len(history[:-1])
            if baseline > 0 and count >= baseline * 3:
                logger.warning(
                    f"[AxiomPriceFeed] 🔥 USER SPIKE: {ticker} — "
                    f"{count} users ({count/baseline:.1f}x baseline)"
                )

    async def subscribe_active_users_for_token(self, ws, token_address: str, ticker: str):
        """Subscribe to active user count updates for a token."""
        def make_user_callback(addr, sym):
            async def _on_users(count: int):
                await self._handle_user_count_update(addr, sym, count)
            return _on_users

        try:
            await ws.subscribe_active_users(
                make_user_callback(token_address, ticker),
                token_address=token_address
            )
            logger.debug(f"[AxiomPriceFeed] Subscribed to user count for {ticker}")
        except Exception as e:
            logger.debug(f"[AxiomPriceFeed] User count subscribe failed for {ticker}: {e}")

    def get_stats(self) -> dict:
        return {
            "subscribed_tokens":     len(self._subscribed),
            "price_updates_received": self.price_updates_received,
            "cache_size":            len(self.price_cache),
        }
