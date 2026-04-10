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
import time
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
        self.price_timestamps: Dict[str, float] = {}   # Unix timestamp of last update
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

        # External price callbacks — registered by DipWatcher or other components.
        # Each callback is called synchronously with (token_address, price_usd) on
        # every tick for subscribed tokens.
        self._price_callbacks: list = []

        self.user_cache: Dict[str, int] = {}
        self._user_baseline_window: Dict[str, list] = {}  # rolling window of last 5 readings
        self._user_count_spikes: Set[str] = set()          # tokens with a 3x user spike

    def register_price_callback(self, callback):
        """
        Register a callable(token_address, price_usd) invoked on every price tick.
        Callbacks must be synchronous and fast — they run inside the WebSocket loop.
        """
        self._price_callbacks.append(callback)

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
        _addr_lower = token_address.lower()
        self._pending_unsubscribe.add(token_address)
        self._subscribed.discard(token_address)
        self.price_cache.pop(_addr_lower, None)
        self.price_timestamps.pop(_addr_lower, None)
        self.user_cache.pop(_addr_lower, None)
        self._user_baseline_window.pop(_addr_lower, None)
        self._user_count_spikes.discard(_addr_lower)
        self.volume_cache.pop(_addr_lower, None)
        self.liquidity_cache.pop(_addr_lower, None)
        self.change_cache.pop(_addr_lower, None)
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
        Connect to the Axiom token-price WebSocket and subscribe to all tracked tokens.

        Primary path: Cloudflare Worker proxy (/ws-proxy) — the Worker runs on
        Cloudflare's own network and can reach cluster9 without Railway's datacenter IP
        being blocked.  Railway can always reach workers.dev domains.

        Fallback: axiomtradeapi library directly (may be blocked on Railway).
        """
        import os
        import json as _json
        import urllib.parse as _up

        token_valid = await self.auth.ensure_valid_token()
        if not token_valid:
            raise Exception("Could not obtain valid Axiom token")

        relay_url    = os.environ.get("AXIOM_REFRESH_RELAY_URL", "")
        relay_secret = os.environ.get("AXIOM_REFRESH_RELAY_SECRET", "")
        worker_base  = relay_url.replace("/refresh", "").rstrip("/") if relay_url else ""

        if worker_base and relay_secret:
            await self._worker_proxy_stream(worker_base, relay_secret)
            return

        # ── Fallback: library WebSocket (direct to socket8 / cluster9) ──────────
        logger.warning(
            "[AxiomPriceFeed] AXIOM_REFRESH_RELAY_URL not set — "
            "connecting directly (may be blocked on Railway)"
        )
        client = self.auth.get_client()
        if not client:
            raise Exception("Could not create AxiomTradeClient")

        ws = client.get_websocket_client()
        self._ws = ws

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
            await self.subscribe_active_users_for_token(ws, token_address, ticker)

        logger.info(
            f"[AxiomPriceFeed] Connected (direct) — subscribed to {len(self._subscribed)} token(s)"
        )
        await ws.start()
        raise Exception("Price feed WebSocket closed cleanly")

    async def _worker_proxy_stream(self, worker_base: str, secret: str):
        """
        Connect to Axiom's price WebSocket via the Cloudflare Worker relay.

        The Worker at /ws-proxy is a full bidirectional relay — it connects to
        cluster9 from Cloudflare's network and forwards all messages both ways.
        We speak the raw Axiom protocol directly over this tunnel:
          subscribe: {"action": "join", "room": "<token_address>"}
          price msgs: {"room": "<token_address>", "content": {priceUsd, ...}}
          user msgs:  {"room": "e-<token_address>", "content": "<count>"}
        """
        import json as _json
        import urllib.parse as _up
        import websockets as _ws_lib

        access  = self.auth.auth_token    or ""
        refresh = self.auth.refresh_token or ""
        qs = _up.urlencode({
            "s":             secret,
            "access_token":  access,
            "refresh_token": refresh,
            "target":        "socket8",   # price feed lives on socket8.axiom.trade
        })
        ws_base   = worker_base.replace("https://", "wss://").replace("http://", "ws://")
        proxy_url = f"{ws_base}/ws-proxy?{qs}"

        logger.info("[AxiomPriceFeed] Connecting via Cloudflare Worker proxy (socket8)")

        try:
            async with _ws_lib.connect(proxy_url) as ws:

                # Subscribe all pending + existing tokens
                all_tokens = self._subscribed | self._pending_subscribe
                self._pending_subscribe.clear()
                for addr in list(all_tokens):
                    if addr in self._pending_unsubscribe:
                        self._pending_unsubscribe.discard(addr)
                        continue
                    await ws.send(_json.dumps({"action": "join", "room": addr}))
                    await ws.send(_json.dumps({"action": "join", "room": f"e-{addr}"}))
                    self._subscribed.add(addr)

                n = len(self._subscribed)
                logger.info(
                    f"[AxiomPriceFeed] Worker proxy connected — "
                    f"subscribed to {n} token(s)" if n else
                    "[AxiomPriceFeed] Worker proxy connected — no tokens yet, "
                    "will subscribe as positions open"
                )

                # Background task: flush pending subscribe/unsubscribe every second
                async def _sub_manager():
                    while True:
                        await asyncio.sleep(1)
                        for addr in list(self._pending_subscribe):
                            self._pending_subscribe.discard(addr)
                            if addr in self._pending_unsubscribe:
                                self._pending_unsubscribe.discard(addr)
                                continue
                            try:
                                await ws.send(_json.dumps({"action": "join", "room": addr}))
                                await ws.send(_json.dumps({"action": "join", "room": f"e-{addr}"}))
                                self._subscribed.add(addr)
                                logger.info(f"[AxiomPriceFeed] Subscribed: {addr[:8]}…")
                            except Exception:
                                self._pending_subscribe.add(addr)  # retry next tick
                        for addr in list(self._pending_unsubscribe):
                            self._pending_unsubscribe.discard(addr)
                            self._subscribed.discard(addr)
                            try:
                                await ws.send(_json.dumps({"action": "leave", "room": addr}))
                            except Exception:
                                pass

                sub_task = asyncio.ensure_future(_sub_manager())
                try:
                    async for message in ws:
                        try:
                            data  = _json.loads(message)
                            room  = data.get("room", "")
                            content = data.get("content")
                            if content is None:
                                continue

                            # Normalize to lowercase — Axiom echoes the canonical
                            # mixed-case Solana address but _subscribed stores lowercase.
                            room_lower = room.lower()

                            if room_lower in self._subscribed:
                                # Price update
                                ticker = self.price_cache.get("__ticker_" + room_lower, room_lower[:8])
                                payload = content if isinstance(content, dict) else {}
                                await self._handle_price_update(room_lower, ticker, payload)

                            elif room.startswith("e-"):
                                # Active user count update
                                token_addr = room[2:].lower()
                                if token_addr in self._subscribed:
                                    try:
                                        await self._handle_user_count_update(
                                            token_addr, token_addr[:8], int(content)
                                        )
                                    except (ValueError, TypeError):
                                        pass

                        except _json.JSONDecodeError:
                            pass
                        except Exception as e:
                            logger.debug(f"[AxiomPriceFeed] Message error: {e}")
                finally:
                    sub_task.cancel()

        except Exception as e:
            err = str(e).lower()
            if "401" in err or "unauthorized" in err:
                raise Exception(f"Worker proxy auth failed: {e}")
            raise Exception(f"Worker proxy WebSocket error: {e}")

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

            _addr_lower = token_address.lower()
            self.price_cache[_addr_lower] = price_usd
            self.price_timestamps[_addr_lower] = time.time()

            # Event-driven stop loss — fires immediately on breach instead of
            # waiting up to 3s for the position manager poll cycle
            if self.position_manager is not None:
                self.position_manager.check_stop_loss_realtime(token_address, price_usd)

            # Notify registered callbacks (e.g. DipWatcher)
            for cb in self._price_callbacks:
                try:
                    cb(token_address, price_usd)
                except Exception as _cb_err:
                    logger.debug(f"[AxiomPriceFeed] Price callback error: {_cb_err}")

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
                self.volume_cache[_addr_lower] = volume_usd
            if liquidity_usd > 0:
                self.liquidity_cache[_addr_lower] = liquidity_usd
            if change_pct != 0:
                self.change_cache[_addr_lower] = change_pct

            logger.debug(
                f"[AxiomPriceFeed] {ticker}: ${price_usd:.8f}"
            )

            # Update trader's open position current_price and pnl in real-time
            if self.trader and hasattr(self.trader, "open_positions"):
                position = self.trader.open_positions.get(token_address.lower())
                if position and hasattr(position, "current_price_usd"):
                    position.current_price_usd = price_usd
                    entry = getattr(position, "entry_price_usd", 0)
                    size  = getattr(position, "amount_usd", 0)
                    if entry > 0 and size > 0:
                        position.pnl_usd = (price_usd / entry - 1) * size

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
                self._user_count_spikes.add(token_address)
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
