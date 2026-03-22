"""
Axiom Real-Time Scanner
Replaces DexScreener polling with Axiom's WebSocket push feed.

Instead of asking DexScreener for tokens every 10-30 seconds,
Axiom pushes new tokens to you the moment they appear on-chain.

This is a DROP-IN replacement for multi_source_scanner.py's polling loop.
DexScreener is kept as a fallback if the Axiom connection drops.

Integration into main.py:
    from feeds.axiom_scanner import AxiomScanner

    axiom_scanner = AxiomScanner(
        auth_token=config.axiom_auth_token,
        refresh_token=config.axiom_refresh_token,
        trader=sol_trader,
        signal_evaluator=signal_evaluator,
        security_checker=security,
        telegram=telegram,
        tracker=tracker,
        market_monitor=market_monitor,
        min_mcap=config.min_mcap,
        max_mcap=config.max_mcap,
        min_score=config.min_combined_score
    )
    tasks.append(axiom_scanner.run())
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# Attempt to import axiomtradeapi — graceful fallback if not installed
try:
    from axiomtradeapi import AxiomTradeClient
    from axiomtradeapi.auth import AxiomAuth
    from axiomtradeapi.exceptions import APIError, NetworkError, AuthenticationError
    AXIOM_AVAILABLE = True
except ImportError:
    AXIOM_AVAILABLE = False
    logger.warning(
        "[AxiomScanner] axiomtradeapi not installed. "
        "Run: pip install axiomtradeapi\n"
        "Falling back to DexScreener polling."
    )


class AxiomTokenEvent:
    """Normalized token event from Axiom WebSocket."""
    def __init__(self, raw: dict):
        self.token_address  = raw.get("tokenAddress", "")
        self.token_symbol   = raw.get("tokenTicker", "?")
        self.token_name     = raw.get("tokenName", "Unknown")
        self.mcap_sol       = float(raw.get("marketCapSol", 0) or 0)
        self.volume_sol     = float(raw.get("volumeSol", 0) or 0)
        self.liquidity_sol  = float(raw.get("liquiditySol", 0) or 0)
        self.protocol       = raw.get("protocol", "unknown")
        self.has_twitter    = bool(raw.get("twitter"))
        self.has_telegram   = bool(raw.get("telegram"))
        self.has_website    = bool(raw.get("website"))
        self.created_at     = raw.get("createdAt", "")
        self.chain_id       = "solana"

    @property
    def mcap_usd(self) -> float:
        """Approximate USD value (SOL price fetched separately)."""
        return self.mcap_sol * 150.0  # Approximation — bot has real SOL price

    @property
    def liquidity_usd(self) -> float:
        return self.liquidity_sol * 150.0

    @property
    def has_socials(self) -> bool:
        return self.has_twitter or self.has_telegram

    def passes_basic_filters(self, min_mcap_usd: float,
                              max_mcap_usd: float,
                              min_liquidity_usd: float = 50_000) -> bool:
        """Quick pre-filter before full signal evaluation."""
        return (
            bool(self.token_address) and
            min_mcap_usd <= self.mcap_usd <= max_mcap_usd and
            self.liquidity_usd >= min_liquidity_usd and
            self.protocol.lower() in ("raydium", "orca", "meteora", "pump.fun")
        )

    def to_dexscreener_format(self) -> dict:
        """
        Convert to a format compatible with the existing signal evaluator.
        This lets AxiomScanner feed into the same scoring pipeline
        without changing signal_evaluator.py at all.
        """
        return {
            "chainId": "solana",
            "baseToken": {
                "address": self.token_address,
                "symbol": self.token_symbol,
                "name": self.token_name
            },
            "marketCap": self.mcap_usd,
            "liquidity": {"usd": self.liquidity_usd},
            "volume": {
                "h1": self.volume_sol * 150.0,
                "h6": 0,
                "h24": 0,
                "m5": 0
            },
            "priceChange": {"m5": 0, "h1": 0, "h6": 0, "h24": 0},
            "txns": {
                "m5": {"buys": 0, "sells": 0},
                "h1": {"buys": 0, "sells": 0}
            },
            "info": {
                "socials": (
                    [{"type": "twitter", "url": ""}] if self.has_twitter else []
                ) + (
                    [{"type": "telegram", "url": ""}] if self.has_telegram else []
                )
            },
            "pairCreatedAt": None
        }


class AxiomAuthManager:
    """
    Manages Axiom authentication tokens.
    Handles login, token refresh, and expiry detection.
    Reads credentials from environment variables.
    """

    def __init__(self,
                 email: Optional[str] = None,
                 password: Optional[str] = None,
                 auth_token: Optional[str] = None,
                 refresh_token: Optional[str] = None):

        # Priority: constructor args → environment variables
        self.email         = email or os.environ.get("AXIOM_EMAIL", "")
        self.password      = password or os.environ.get("AXIOM_PASSWORD", "")
        self.auth_token    = auth_token or os.environ.get("AXIOM_AUTH_TOKEN", "")
        self.refresh_token = refresh_token or os.environ.get("AXIOM_REFRESH_TOKEN", "")
        self._auth         = AxiomAuth() if AXIOM_AVAILABLE else None

    @property
    def has_credentials(self) -> bool:
        return bool((self.email and self.password) or self.auth_token)

    async def ensure_valid_token(self) -> bool:
        """Ensure we have a valid auth token, logging in or refreshing if needed."""
        if not AXIOM_AVAILABLE:
            return False

        # Try refresh first if we have a refresh token
        if self.refresh_token:
            try:
                result = await self._auth.refresh_tokens(self.refresh_token)
                if result.get("success"):
                    self.auth_token    = result["auth_token"]
                    self.refresh_token = result["refresh_token"]
                    logger.info("[AxiomAuth] Token refreshed successfully")
                    return True
            except Exception as e:
                logger.debug(f"[AxiomAuth] Refresh failed: {e} — trying login")

        # Fall back to email/password login
        if self.email and self.password:
            try:
                result = await self._auth.login(self.email, self.password)
                if result.get("success"):
                    self.auth_token    = result["auth_token"]
                    self.refresh_token = result["refresh_token"]
                    logger.info("[AxiomAuth] Logged in successfully")
                    return True
            except Exception as e:
                logger.error(f"[AxiomAuth] Login failed: {e}")
                return False

        if self.auth_token:
            # Have a token but couldn't refresh — try using it as-is
            return True

        logger.error(
            "[AxiomAuth] No credentials available. "
            "Set AXIOM_EMAIL + AXIOM_PASSWORD or AXIOM_AUTH_TOKEN "
            "in Railway Variables."
        )
        return False


class AxiomScanner:
    """
    Real-time token scanner using Axiom's WebSocket feed.

    Replaces the DexScreener polling loop with push-based token discovery.
    New tokens arrive the moment they appear on Raydium/Orca/Pump.fun —
    no waiting for the next poll cycle.

    Architecture:
      Axiom WebSocket → token filter → signal evaluator → security check
      → trader.buy() (same pipeline as DexScreener scanner)

    Falls back to DexScreener polling if Axiom connection is unavailable.
    """

    def __init__(self,
                 auth_manager: AxiomAuthManager,
                 trader,
                 signal_evaluator,
                 security_checker,
                 telegram,
                 tracker,
                 market_monitor=None,

                 # Token filters
                 min_mcap_usd: float = 200_000,
                 max_mcap_usd: float = 1_000_000,
                 min_liquidity_usd: float = 50_000,
                 min_score: float = 65.0,

                 # Behavior
                 reconnect_delay_seconds: int = 10,
                 fallback_to_dexscreener: bool = True):

        self.auth          = auth_manager
        self.trader        = trader
        self.evaluator     = signal_evaluator
        self.security      = security_checker
        self.telegram      = telegram
        self.tracker       = tracker
        self.market_monitor = market_monitor

        self.min_mcap      = min_mcap_usd
        self.max_mcap      = max_mcap_usd
        self.min_liquidity = min_liquidity_usd
        self.min_score     = min_score
        self.reconnect_delay = reconnect_delay_seconds
        self.fallback      = fallback_to_dexscreener

        # State
        self._client: Optional[AxiomTradeClient] = None
        self._seen_tokens: set = set()
        self._running = False

        # Stats
        self.tokens_received    = 0
        self.tokens_passed_filter = 0
        self.tokens_evaluated   = 0
        self.signals_fired      = 0
        self.reconnect_count    = 0

    async def run(self):
        """
        Main scanner loop.
        Connects to Axiom WebSocket and processes real-time token feed.
        Falls back to DexScreener polling if connection fails.
        """
        if not AXIOM_AVAILABLE:
            logger.warning(
                "[AxiomScanner] axiomtradeapi not available — "
                "run: pip install axiomtradeapi"
            )
            if self.fallback:
                await self._run_dexscreener_fallback()
            return

        if not self.auth.has_credentials:
            logger.warning(
                "[AxiomScanner] No Axiom credentials configured. "
                "Set AXIOM_EMAIL and AXIOM_PASSWORD in Railway Variables. "
                "Falling back to DexScreener polling."
            )
            if self.fallback:
                await self._run_dexscreener_fallback()
            return

        self._running = True
        logger.info(
            "[AxiomScanner] Starting real-time token feed | "
            f"MCap: ${self.min_mcap/1000:.0f}k-${self.max_mcap/1000:.0f}k | "
            f"Min score: {self.min_score}"
        )

        while self._running:
            try:
                await self._connect_and_stream()
            except AuthenticationError as e:
                logger.warning(f"[AxiomScanner] Auth expired — refreshing: {e}")
                await self.auth.ensure_valid_token()
                await asyncio.sleep(2)
            except NetworkError as e:
                logger.warning(
                    f"[AxiomScanner] Network error — reconnecting in "
                    f"{self.reconnect_delay}s: {e}"
                )
                self.reconnect_count += 1
                await asyncio.sleep(self.reconnect_delay)
            except Exception as e:
                logger.error(f"[AxiomScanner] Unexpected error: {e}")
                self.reconnect_count += 1
                await asyncio.sleep(self.reconnect_delay)

    async def _connect_and_stream(self):
        """Establish connection and stream tokens until disconnected."""
        # Refresh token before connecting
        token_valid = await self.auth.ensure_valid_token()
        if not token_valid:
            raise AuthenticationError("Could not obtain valid token")

        self._client = AxiomTradeClient(
            auth_token=self.auth.auth_token,
            refresh_token=self.auth.refresh_token,
            max_retries=3
        )

        logger.info("[AxiomScanner] Connected to Axiom WebSocket feed")
        await self.telegram.send(
            "🔌 *Axiom Scanner Connected*\n"
            "Real-time token feed active — no more polling delays"
        )

        await self._client.subscribe_new_tokens(self._handle_token_batch)
        await self._client.ws.start()

    async def _handle_token_batch(self, raw_tokens: list):
        """Process a batch of tokens from Axiom WebSocket."""
        for raw in raw_tokens:
            await self._process_token(raw)

    async def _process_token(self, raw: dict):
        """Full processing pipeline for one incoming token."""
        try:
            event = AxiomTokenEvent(raw)
            self.tokens_received += 1

            # Skip if already seen
            if event.token_address in self._seen_tokens:
                return
            self._seen_tokens.add(event.token_address)

            # Keep seen set bounded
            if len(self._seen_tokens) > 10_000:
                self._seen_tokens = set(list(self._seen_tokens)[-5_000:])

            # Basic filter — quick and cheap
            if not event.passes_basic_filters(
                self.min_mcap, self.max_mcap, self.min_liquidity
            ):
                return

            self.tokens_passed_filter += 1

            # Market condition gate
            if self.market_monitor and self.market_monitor.market_restricted:
                if not self.market_monitor.should_trade(signal_score=0):
                    return

            # Security gate — runs GoPlus check
            if self.security:
                sec_result = await self.security.check_token(
                    event.token_address, "solana"
                )
                if sec_result and sec_result.is_blocked:
                    logger.debug(
                        f"[AxiomScanner] Security blocked: "
                        f"{event.token_symbol} — {sec_result.risk_level}"
                    )
                    return

            # Signal evaluation — same evaluator as DexScreener scanner
            self.tokens_evaluated += 1
            pair_data = event.to_dexscreener_format()

            if self.evaluator:
                evaluation = await self.evaluator.evaluate(pair_data)

                if evaluation.hard_skip:
                    logger.debug(
                        f"[AxiomScanner] Hard skip: {event.token_symbol} — "
                        f"{', '.join(evaluation.skip_reasons)}"
                    )
                    return

                score = evaluation.total_score
                effective_min = self.min_score

                if self.market_monitor and self.market_monitor.market_restricted:
                    effective_min = self.market_monitor.restricted_threshold

                if score < effective_min:
                    return
            else:
                # No evaluator — use basic liquidity/social check
                score = 65
                if not event.has_socials:
                    return

            # Signal fires
            self.signals_fired += 1
            logger.info(
                f"[AxiomScanner] 🚀 SIGNAL: {event.token_symbol} | "
                f"MCap: ${event.mcap_usd:,.0f} | "
                f"Score: {score:.0f} | "
                f"Protocol: {event.protocol}"
            )

            await self.telegram.send(
                f"🚀 *Axiom Signal* [Solana]\n\n"
                f"🪙 ${event.token_symbol} — {event.token_name}\n"
                f"📊 MCap: ${event.mcap_usd:,.0f}\n"
                f"💧 Liquidity: ${event.liquidity_usd:,.0f}\n"
                f"📈 Volume: ${event.volume_sol * 150:,.0f}\n"
                f"⭐ Score: {score:.0f}/100\n"
                f"🔗 Protocol: {event.protocol}\n"
                f"⚡ Real-time via Axiom WebSocket"
            )

            # Execute buy via existing trader
            await self.trader.buy(
                token_address=event.token_address,
                token_symbol=event.token_symbol,
                reason=f"Axiom real-time signal | score {score:.0f} | {event.protocol}",
                signal_score=int(score),
                hh_hl_confirmed=getattr(evaluation, "hh_hl_confirmed", False)
                if self.evaluator else False
            )

        except Exception as e:
            logger.error(f"[AxiomScanner] Token processing error: {e}")

    async def _run_dexscreener_fallback(self):
        """
        Fallback polling mode using DexScreener.
        Used when Axiom is unavailable.
        Logs a warning so you know it's running in fallback mode.
        """
        import aiohttp
        import socket

        logger.warning(
            "[AxiomScanner] Running in DexScreener fallback mode. "
            "Add AXIOM_EMAIL and AXIOM_PASSWORD to Railway Variables "
            "to enable real-time feed."
        )

        await self.telegram.send(
            "⚠️ *Axiom Scanner — Fallback Mode*\n"
            "Running on DexScreener polling (10s interval).\n"
            "Add AXIOM_EMAIL + AXIOM_PASSWORD to Railway Variables "
            "to enable real-time feed."
        )

        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        while True:
            try:
                async with aiohttp.ClientSession(connector=connector) as session:
                    url = (
                        "https://api.dexscreener.com/latest/dex/search"
                        "?q=solana&order=trending"
                    )
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            pairs = [
                                p for p in data.get("pairs", [])
                                if p.get("chainId") == "solana"
                            ]
                            for pair in pairs[:20]:
                                token_addr = pair.get(
                                    "baseToken", {}
                                ).get("address", "")
                                if token_addr and token_addr not in self._seen_tokens:
                                    self._seen_tokens.add(token_addr)
                                    raw = {
                                        "tokenAddress": token_addr,
                                        "tokenTicker": pair.get(
                                            "baseToken", {}
                                        ).get("symbol", "?"),
                                        "tokenName": pair.get(
                                            "baseToken", {}
                                        ).get("name", ""),
                                        "marketCapSol": (
                                            pair.get("marketCap", 0) or 0
                                        ) / 150,
                                        "liquiditySol": (
                                            pair.get("liquidity", {}).get("usd", 0)
                                            or 0
                                        ) / 150,
                                        "volumeSol": (
                                            pair.get("volume", {}).get("h1", 0)
                                            or 0
                                        ) / 150,
                                        "protocol": "raydium",
                                        "twitter": any(
                                            s.get("type") == "twitter"
                                            for s in pair.get(
                                                "info", {}
                                            ).get("socials", [])
                                        ),
                                        "telegram": any(
                                            s.get("type") == "telegram"
                                            for s in pair.get(
                                                "info", {}
                                            ).get("socials", [])
                                        ),
                                    }
                                    await self._process_token(raw)
            except Exception as e:
                logger.debug(f"[AxiomScanner] Fallback error: {e}")

            await asyncio.sleep(10)

    def get_stats(self) -> dict:
        return {
            "scanner": "axiom_realtime"
            if (AXIOM_AVAILABLE and self.auth.has_credentials)
            else "dexscreener_fallback",
            "tokens_received":      self.tokens_received,
            "tokens_passed_filter": self.tokens_passed_filter,
            "tokens_evaluated":     self.tokens_evaluated,
            "signals_fired":        self.signals_fired,
            "reconnect_count":      self.reconnect_count,
            "seen_tokens":          len(self._seen_tokens)
        }
