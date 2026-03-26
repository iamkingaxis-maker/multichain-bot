"""
Axiom Smart Wallet WebSocket Tracker — Phase 2
Subscribes to real-time transaction feeds for known "smart" copy wallets via
Axiom's WebSocket. When a tracked wallet buys a token we haven't seen,
evaluate it and optionally fire a signal.

This is separate from the existing AxiomWalletTracker (which polls SOL balances).
This one uses the subscribe_wallet_transactions WebSocket for real-time buy signals.
"""

import asyncio
import logging
import time as _time
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

try:
    from axiomtradeapi import AxiomTradeClient, AxiomTradeWebSocketClient
    AXIOM_AVAILABLE = True
except ImportError:
    AXIOM_AVAILABLE = False


class AxiomSmartWalletTracker:
    """
    Subscribes to Axiom's wallet transaction WebSocket for a list of
    known smart/copy wallets. When any tracked wallet makes a buy,
    the token is evaluated and a signal may fire.

    Complements the existing AxiomWalletTracker (balance polling) with
    real-time buy detection.
    """

    def __init__(self,
                 auth_manager,
                 trader,
                 signal_evaluator,
                 security_checker,
                 telegram,
                 tracker,
                 market_monitor=None,
                 wallets: List[str] = None,
                 min_score: float = 65.0):

        self.auth           = auth_manager
        self.trader         = trader
        self.evaluator      = signal_evaluator
        self.security       = security_checker
        self.telegram       = telegram
        self.tracker        = tracker
        self.market_monitor = market_monitor
        self.wallets        = wallets or []
        self.min_score      = min_score

        self._seen_tokens: set = set()
        self._reconnect_delay = 10

        # Set by connect_to_bot() — routes buys through chart analysis gate
        self.scanner = None

        # Stats
        self.wallet_buys_seen = 0
        self.tokens_evaluated = 0
        self.signals_fired    = 0

        self._wallet_positions: Dict[str, set] = {}
        self._position_check_interval = 60

    async def run(self):
        """Main loop — connects WebSocket, subscribes all wallets, reconnects on drop."""
        if not AXIOM_AVAILABLE:
            logger.warning(
                "[AxiomWallets] axiomtradeapi not available — wallet tracker disabled"
            )
            return

        if not self.auth.has_credentials:
            logger.warning(
                "[AxiomWallets] No Axiom credentials — smart wallet tracker disabled"
            )
            return

        if not self.wallets:
            logger.info(
                "[AxiomWallets] No wallets configured — "
                "set solana_copy_wallets in config or pass wallets= to constructor"
            )
            return

        logger.info(
            f"[AxiomWallets] Starting | tracking {len(self.wallets)} wallets | "
            f"min_score={self.min_score}"
        )

        asyncio.create_task(self._position_monitor_loop())

        _backoff = self._reconnect_delay
        _auth_failures = 0
        _MAX_AUTH_FAILURES = 3

        while True:
            try:
                await self._connect_and_stream()
                _backoff = self._reconnect_delay  # reset on clean close
                _auth_failures = 0
            except Exception as e:
                err = str(e).lower()
                is_auth = any(k in err for k in ("auth", "401", "403", "token", "login", "credential"))
                if is_auth:
                    _auth_failures += 1
                    if _auth_failures >= _MAX_AUTH_FAILURES:
                        logger.warning(
                            f"[AxiomWallets] Auth failed {_auth_failures} times — "
                            "token expired or invalid. Pausing 30 min. "
                            "Update AXIOM_AUTH_TOKEN + AXIOM_REFRESH_TOKEN in Railway Variables to restore."
                        )
                        await asyncio.sleep(1800)  # 30 min — stop spamming
                        _auth_failures = 0
                        continue
                    logger.warning(
                        f"[AxiomWallets] Auth error ({_auth_failures}/{_MAX_AUTH_FAILURES}) — "
                        f"retrying in 120s: {e}"
                    )
                    await asyncio.sleep(120)
                else:
                    logger.warning(
                        f"[AxiomWallets] Disconnected — reconnecting in {_backoff}s: {e}"
                    )
                    await asyncio.sleep(_backoff)
                    _backoff = min(_backoff * 2, 300)

    async def _connect_and_stream(self):
        """Establish connection, subscribe all wallets, stream until disconnect."""
        token_valid = await self.auth.ensure_valid_token()
        if not token_valid:
            raise Exception("Could not obtain valid Axiom token")

        client = self.auth.get_client()
        if not client:
            raise Exception("Could not create AxiomTradeClient")

        ws = client.get_websocket_client()

        logger.info(
            f"[AxiomWallets] Subscribing to {len(self.wallets)} wallet feeds"
        )

        # Subscribe to each wallet BEFORE calling start()
        for wallet_address in self.wallets:
            # Build a closure to capture wallet_address correctly
            def make_callback(addr):
                async def _on_tx(tx_data: dict):
                    await self._handle_transaction(addr, tx_data)
                return _on_tx

            await ws.subscribe_wallet_transactions(
                wallet_address, make_callback(wallet_address)
            )

        logger.info(
            f"[AxiomWallets] All wallet subscriptions active — listening for buys"
        )

        # start() blocks until disconnect
        await ws.start()

        # If start() returns cleanly, connection closed — trigger reconnect
        raise Exception("WebSocket closed cleanly")

    async def _handle_transaction(self, wallet_address: str, tx_data: dict):
        """
        Process a wallet transaction event from Axiom WebSocket.

        Expected tx_data structure (from subscribe_wallet_transactions docstring):
        {
            "created_at": "...",
            "type": "buy" or "sell",
            "total_sol": <SOL amount>,
            "total_usd": <USD amount>,
            "maker_address": "<wallet>",
            "pair_address": "<pair address>",
            "pair": {
                "tokenAddress": "<token address>",
                "tokenName": "<name>",
                "tokenTicker": "<ticker>",
                "protocol": "<protocol>"
            }
        }
        """
        try:
            tx_type = tx_data.get("type", "").lower()
            if tx_type != "buy":
                return

            total_sol = float(tx_data.get("total_sol") or 0)
            if total_sol < 0.1:
                return  # dust transaction — skip

            pair_info = tx_data.get("pair") or {}
            token_address = pair_info.get("tokenAddress") or ""
            ticker = pair_info.get("tokenTicker") or "?"
            token_name = pair_info.get("tokenName") or ticker

            if not token_address:
                return

            # Staleness check — skip replayed/old transactions (older than 60s)
            created_at_raw = tx_data.get("created_at") or tx_data.get("createdAt") or ""
            if created_at_raw:
                try:
                    ts_ms = float(created_at_raw)
                    age_sec = (_time.time() * 1000 - ts_ms) / 1000
                    if age_sec > 60:
                        logger.debug(f"[AxiomWallets] Skipping stale tx ({age_sec:.0f}s old)")
                        return
                    logger.debug(f"[AxiomWallets] Tx latency: {age_sec:.1f}s")
                except (ValueError, TypeError):
                    pass

            self.wallet_buys_seen += 1

            if token_address in self._seen_tokens:
                return

            self._seen_tokens.add(token_address)

            # Keep seen set bounded
            if len(self._seen_tokens) > 20_000:
                self._seen_tokens = set(list(self._seen_tokens)[-10_000:])

            total_usd = float(tx_data.get("total_usd") or 0)
            logger.info(
                f"[AxiomWallets] {wallet_address[:8]}... bought {ticker} "
                f"({total_sol:.2f} SOL / ${total_usd:,.0f}) — evaluating"
            )

            fired = await self._evaluate_token(
                token_address=token_address,
                ticker=ticker,
                token_name=token_name,
                pair_address=tx_data.get("pair_address") or "",
                deployer_address="",  # not available in wallet tx feed
                wallet_address=wallet_address,
                total_sol=total_sol,
            )
            if fired:
                self.signals_fired += 1
                if wallet_address not in self._wallet_positions:
                    self._wallet_positions[wallet_address] = set()
                    asyncio.create_task(self._check_wallet_positions(wallet_address))

        except Exception as e:
            logger.error(f"[AxiomWallets] Transaction handler error: {e}")

    async def _evaluate_token(self,
                               token_address: str,
                               ticker: str,
                               token_name: str,
                               pair_address: str,
                               deployer_address: str,
                               wallet_address: str,
                               total_sol: float) -> bool:
        """
        Full pipeline: security → enrichment → DexScreener → evaluator → buy.
        Returns True if a signal fired.
        """
        try:
            # Market condition gate
            if self.market_monitor and self.market_monitor.market_restricted:
                if not self.market_monitor.should_trade(signal_score=0):
                    return False

            # Security gate
            if self.security:
                sec_result = await self.security.check(token_address, "solana")
                if sec_result and not sec_result.passed:
                    logger.info(
                        f"[AxiomWallets] Security blocked: {ticker} — "
                        f"{sec_result.risk_level}"
                    )
                    return False

            # Enrichment check (holder concentration + dev history)
            if pair_address:
                from feeds.axiom_scanner import axiom_enrich_check
                passed, reason = await axiom_enrich_check(
                    self.auth, pair_address, deployer_address
                )
                if not passed:
                    logger.info(
                        f"[AxiomWallets] Enrich blocked: {ticker} — {reason}"
                    )
                    return False

            # DexScreener data fetch
            pair_data = await self._fetch_dexscreener_pair(token_address)
            if pair_data is None:
                logger.debug(
                    f"[AxiomWallets] No DexScreener data for {ticker} — skipping"
                )
                return False

            self.tokens_evaluated += 1

            # Full signal evaluation
            if self.evaluator:
                evaluation = await self.evaluator.evaluate(pair_data)
                if evaluation.hard_skip:
                    logger.debug(
                        f"[AxiomWallets] Hard skip: {ticker} — "
                        f"{', '.join(evaluation.skip_reasons)}"
                    )
                    return False
                score = evaluation.total_score
                effective_min = self.min_score
                if self.market_monitor and self.market_monitor.market_restricted:
                    effective_min = self.market_monitor.restricted_threshold
                if score < effective_min:
                    return False
            else:
                score = 70

            # Signal fires
            mcap   = pair_data.get("marketCap") or 0
            liq    = (pair_data.get("liquidity") or {}).get("usd") or 0
            logger.info(
                f"[AxiomWallets] SIGNAL from wallet {wallet_address[:8]}: "
                f"{ticker} | Score: {score:.0f}"
            )

            if self.scanner:
                # Route through scanner's chart analysis — no buy on score alone
                bought = await self.scanner.process_external_signal(
                    token_address=token_address,
                    token_symbol=ticker,
                    reason=f"Axiom wallet signal | wallet {wallet_address[:8]} | score {score:.0f}",
                    signal_score=int(score),
                    strategy_tag="AxiomWallet",
                    skip_security=True,
                    price_usd=float(pair_data.get("priceUsd") or 0),
                    liquidity_usd=liq,
                    volume_h1=float((pair_data.get("volume") or {}).get("h1") or 0),
                )
                return bought
            else:
                await self.telegram.send(
                    f"👛 *Axiom Wallet Signal* [Solana]\n\n"
                    f"🪙 ${ticker} — {token_name}\n"
                    f"📊 MCap: ${mcap:,.0f}\n"
                    f"💧 Liquidity: ${liq:,.0f}\n"
                    f"⭐ Score: {score:.0f}/100\n"
                    f"👤 Wallet: `{wallet_address[:8]}...`\n"
                    f"💰 Wallet spent: {total_sol:.2f} SOL"
                )
                await self.trader.buy(
                    token_address=token_address,
                    token_symbol=ticker,
                    reason=(
                        f"Axiom wallet signal | wallet {wallet_address[:8]} | "
                        f"score {score:.0f}"
                    ),
                    signal_score=int(score),
                    hh_hl_confirmed=getattr(evaluation, "hh_hl_confirmed", False)
                    if self.evaluator else False
                )
                return True

        except Exception as e:
            logger.error(f"[AxiomWallets] Evaluate error for {ticker}: {e}")
            return False

    async def _fetch_dexscreener_pair(self, token_address: str) -> Optional[dict]:
        """Fetch best Solana pair from DexScreener."""
        import aiohttp
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=6)
                ) as resp:
                    data = await resp.json(content_type=None)
                    pairs = [
                        p for p in (data.get("pairs") or [])
                        if p.get("chainId") == "solana"
                    ]
                    if not pairs:
                        return None
                    return max(pairs, key=lambda p: (
                        p.get("liquidity", {}).get("usd") or 0
                    ))
        except Exception as e:
            logger.debug(
                f"[AxiomWallets] DexScreener fetch failed for "
                f"{token_address[:8]}: {e}"
            )
            return None

    def get_stats(self) -> dict:
        return {
            "wallets_tracked":  len(self.wallets),
            "wallet_buys_seen": self.wallet_buys_seen,
            "tokens_evaluated": self.tokens_evaluated,
            "signals_fired":    self.signals_fired,
            "seen_tokens":      len(self._seen_tokens),
        }

    async def _check_wallet_positions(self, wallet_address: str):
        """Detect when a tracked wallet closes a position we may also hold."""
        loop = asyncio.get_running_loop()
        try:
            client = self.auth.get_client()
            if not client:
                return
            positions = await loop.run_in_executor(
                None, client.get_meme_open_positions, wallet_address
            )
            if positions is None:
                return

            current_tokens = set()
            for p in (positions or []):
                addr = p.get("tokenAddress") or p.get("token_address") or ""
                if addr:
                    current_tokens.add(addr)

            prev_tokens = self._wallet_positions.get(wallet_address, set())
            closed_tokens = prev_tokens - current_tokens

            for token_addr in closed_tokens:
                logger.info(
                    f"[AxiomWallets] \U0001f6aa Wallet {wallet_address[:8]} closed position: "
                    f"{token_addr[:8]} — consider exiting if we hold it"
                )
                if hasattr(self.trader, 'open_positions') and token_addr in self.trader.open_positions:
                    await self.telegram.send(
                        f"\U0001f45b *Copy Wallet Closed Position* [Solana]\n\n"
                        f"Wallet `{wallet_address[:8]}...` exited `{token_addr[:8]}...`\n"
                        f"We hold this token — consider exiting."
                    )

            self._wallet_positions[wallet_address] = current_tokens

        except Exception as e:
            logger.debug(f"[AxiomWallets] Position check failed for {wallet_address[:8]}: {e}")

    async def _position_monitor_loop(self):
        """Background task: check if tracked wallets closed their positions."""
        while True:
            await asyncio.sleep(self._position_check_interval)
            for wallet in list(self._wallet_positions.keys()):
                await self._check_wallet_positions(wallet)
