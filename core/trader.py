"""
Trader
Handles buy/sell execution on Solana via Jupiter aggregator.
Manages open positions with take-profit and stop-loss logic.
"""

import asyncio
import logging
import aiohttp
import json
import base64
from typing import Dict, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, field

from core.paper_slippage import PaperSlippageSimulator

logger = logging.getLogger(__name__)

# Paid API key endpoints (api.jup.ag) — more reliable, higher rate limits
# Falls back to free tier (quote-api.jup.ag) if no key is set
import os as _os
_JUPITER_API_KEY = _os.environ.get("JUPITER_API_KEY", "")
if _JUPITER_API_KEY:
    JUPITER_QUOTE_API = f"https://api.jup.ag/swap/v1/quote"
    JUPITER_SWAP_API = f"https://api.jup.ag/swap/v1/swap"
    _JUPITER_HEADERS = {"x-api-key": _JUPITER_API_KEY}
else:
    JUPITER_QUOTE_API = "https://quote-api.jup.ag/v6/quote"
    JUPITER_SWAP_API = "https://quote-api.jup.ag/v6/swap"
    _JUPITER_HEADERS = {}
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@dataclass
class Position:
    token_address: str
    token_symbol: str
    entry_price_usd: float
    amount_tokens: float
    amount_sol_spent: float
    entry_time: datetime
    reason: str
    take_profit_1_hit: bool = False
    take_profit_2_hit: bool = False
    current_price_usd: float = 0.0
    pnl_usd: float = 0.0
    # Signal quality at entry — used by PositionManager for pyramid decisions
    signal_score: int = 0
    hh_hl_confirmed: bool = False
    # Metadata for dashboard and tracker
    chain_id: str = "solana"
    amount_usd: float = 0.0   # USD position size at entry (not SOL)
    strategy: str = "scanner"  # Which strategy placed this trade


class Trader:
    def __init__(self, private_key: str, rpc_url: str, tracker, telegram, risk_manager,
                 stop_loss_pct: float = 10.0):
        self.private_key = private_key
        self.rpc_url = rpc_url
        self.tracker = tracker
        self.telegram = telegram
        self.risk_manager = risk_manager
        self.open_positions: Dict[str, Position] = {}
        self.session: Optional[aiohttp.ClientSession] = None

        # Take profit levels (from config)
        self.tp1_multiplier = 2.0    # Sell 50% at 2x
        self.tp2_multiplier = 5.0    # Sell 30% at 5x
        self.tp3_multiplier = 10.0   # Sell rest at 10x
        self.stop_loss_pct = stop_loss_pct

        # Paper trading slippage simulator
        self.paper_slippage = PaperSlippageSimulator("solana")

        # Sell dedup — prevents CopyTrader and PositionManager racing on same token
        self._selling: set = set()

        # Optional Axiom auth — registered externally for Axiom-based price lookups
        self._axiom_auth = None

        # Optional Axiom real-time price feed (Phase 4)
        self._axiom_price_feed = None

        # NOTE: Internal _monitor_positions is DISABLED — PositionManager handles
        # all TP/SL logic with the user's exact config-driven rules.
        # asyncio.create_task(self._monitor_positions())

    def register_axiom_auth(self, auth):
        """Register Axiom auth manager for Axiom-based price lookups."""
        self._axiom_auth = auth

    def register_axiom_price_feed(self, feed):
        """
        Register the AxiomPriceFeed instance for real-time price updates.
        The position manager can call:
            price = self.trader._axiom_price_feed.price_cache.get(token_address)
        before falling back to DexScreener.
        """
        self._axiom_price_feed = feed

    async def buy(self, token_address: str, token_symbol: str,
                  reason: str, signal_score: int = 0,
                  hh_hl_confirmed: bool = False,
                  chain_id: str = "solana", strategy: str = "scanner"):
        """Execute a buy order."""
        position_size_usd = self.risk_manager.get_position_size()
        if position_size_usd <= 0:
            logger.warning(f"Risk manager blocked buy for {token_symbol}")
            return

        logger.info(f"💚 Buying {token_symbol} — ${position_size_usd:.0f} — {reason}")

        try:
            # ── PAPER TRADING MODE ────────────────────────────────────
            if not self.private_key:
                current_price = await self._get_token_price(token_address)
                if current_price <= 0:
                    logger.error(f"Could not get price for {token_symbol} — buy aborted")
                    return
                liquidity_usd = await self._get_token_liquidity(token_address)

                adjusted_price, tokens_received, slip_est = \
                    self.paper_slippage.apply_to_buy(
                        position_size_usd, liquidity_usd,
                        current_price, token_symbol
                    )

                sol_amount = await self._usd_to_sol(position_size_usd)
                position = Position(
                    token_address=token_address,
                    token_symbol=token_symbol,
                    entry_price_usd=adjusted_price,
                    amount_tokens=tokens_received,
                    amount_sol_spent=sol_amount,
                    entry_time=datetime.now(timezone.utc),
                    reason=reason,
                    signal_score=signal_score,
                    hh_hl_confirmed=hh_hl_confirmed,
                    chain_id=chain_id,
                    amount_usd=position_size_usd,
                    strategy=strategy,
                )
                self.open_positions[token_address] = position
                self.risk_manager.record_buy(position_size_usd)

                await self.telegram.send(
                    f"📄 *[PAPER] Bought ${token_symbol}*\n\n"
                    f"💵 Size: ${position_size_usd:.0f}\n"
                    f"💰 Entry: ${adjusted_price:.8f} "
                    f"(+{slip_est.total_slippage_pct:.2f}% slippage)\n"
                    f"🪙 Tokens: {tokens_received:.4f}\n"
                    f"📝 {reason}"
                )
                self.tracker.record_buy(position)
                logger.info(
                    f"📄 [PAPER] Bought {token_symbol} — "
                    f"${position_size_usd:.0f} | "
                    f"Slippage: {slip_est.total_slippage_pct:.2f}%"
                )
                return

            # ── LIVE TRADING MODE ─────────────────────────────────────
            # Get SOL amount for position size
            sol_amount = await self._usd_to_sol(position_size_usd)
            if sol_amount <= 0:
                return

            # Get Jupiter quote
            quote = await self._get_quote(
                input_mint=SOL_MINT,
                output_mint=token_address,
                amount=int(sol_amount * 1e9)  # lamports
            )
            if not quote:
                logger.error(f"No quote available for {token_symbol}")
                return

            # Execute swap
            out_amount = int(quote.get("outAmount", 0))
            entry_price = position_size_usd / (out_amount / 1e9) if out_amount > 0 else 0

            success = await self._execute_swap(quote)
            if not success:
                logger.error(f"Swap failed for {token_symbol}")
                return

            # Record position
            position = Position(
                token_address=token_address,
                token_symbol=token_symbol,
                entry_price_usd=entry_price,
                amount_tokens=out_amount / 1e9,
                amount_sol_spent=sol_amount,
                entry_time=datetime.now(timezone.utc),
                reason=reason,
                signal_score=signal_score,
                hh_hl_confirmed=hh_hl_confirmed,
                chain_id=chain_id,
                amount_usd=position_size_usd,
                strategy=strategy,
            )
            self.open_positions[token_address] = position
            self.risk_manager.record_buy(position_size_usd)

            await self.telegram.send(
                f"✅ *Bought ${token_symbol}*\n\n"
                f"💵 Size: ${position_size_usd:.0f}\n"
                f"📝 Reason: {reason}\n"
                f"🎯 TP1: {self.tp1_multiplier}x | TP2: {self.tp2_multiplier}x | TP3: {self.tp3_multiplier}x\n"
                f"🛑 Stop Loss: -{self.stop_loss_pct*100:.0f}%"
            )
            self.tracker.record_buy(position)
            logger.info(f"✅ Bought {token_symbol} — ${position_size_usd:.0f}")

        except Exception as e:
            logger.error(f"Buy failed for {token_symbol}: {e}")

    async def sell(self, token_address: str, token_symbol: str, reason: str, pct: float = 1.0):
        """Execute a sell order for a percentage of the position."""
        position = self.open_positions.get(token_address)
        if not position:
            logger.warning(f"No position found for {token_symbol}")
            return

        # Prevent concurrent sells on the same token (race between CopyTrader and PositionManager)
        if token_address in self._selling:
            logger.debug(f"[Trader] Sell already in progress for {token_symbol} — skipping duplicate")
            return
        self._selling.add(token_address)

        try:
            # ── PAPER TRADING MODE ────────────────────────────────────
            if not self.private_key:
                current_price = await self._get_token_price(token_address)
                liquidity_usd = await self._get_token_liquidity(token_address)
                tokens_to_sell = position.amount_tokens * pct

                adjusted_price, usd_received, slip_est = \
                    self.paper_slippage.apply_to_sell(
                        tokens_to_sell, liquidity_usd,
                        current_price, token_symbol
                    )

                cost_basis = position.entry_price_usd * tokens_to_sell
                pnl = usd_received - cost_basis
                pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0

                if pct >= 1.0:
                    del self.open_positions[token_address]
                else:
                    position.amount_tokens *= (1 - pct)
                    position.amount_sol_spent *= (1 - pct)

                self.risk_manager.record_sell(usd_received, pnl)
                emoji = "🟢" if pnl >= 0 else "🔴"

                await self.telegram.send(
                    f"{emoji} *[PAPER] Sold ${token_symbol}* ({pct*100:.0f}%)\n\n"
                    f"💵 Received: ${usd_received:.2f}\n"
                    f"📊 PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
                    f"📉 Exit slippage: {slip_est.total_slippage_pct:.2f}%\n"
                    f"📝 {reason}"
                )
                self.tracker.record_sell(token_address, usd_received, pnl, reason, pnl_pct=round(pnl_pct, 2))
                logger.info(
                    f"{emoji} [PAPER] Sold {pct*100:.0f}% of {token_symbol} — "
                    f"PnL: ${pnl:+.2f} | Slippage: {slip_est.total_slippage_pct:.2f}%"
                )
                return

            # ── LIVE TRADING MODE ─────────────────────────────────────
            tokens_to_sell = int(position.amount_tokens * pct * 1e9)

            quote = await self._get_quote(
                input_mint=token_address,
                output_mint=SOL_MINT,
                amount=tokens_to_sell
            )
            if not quote:
                return

            sol_received = int(quote.get("outAmount", 0)) / 1e9
            usd_received = await self._sol_to_usd(sol_received)
            cost_basis = position.amount_usd * pct
            pnl = usd_received - cost_basis
            pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0

            success = await self._execute_swap(quote)
            if not success:
                return

            if pct >= 1.0:
                del self.open_positions[token_address]
            else:
                position.amount_tokens *= (1 - pct)
                position.amount_sol_spent *= (1 - pct)

            self.risk_manager.record_sell(usd_received, pnl)

            emoji = "🟢" if pnl >= 0 else "🔴"
            await self.telegram.send(
                f"{emoji} *Sold ${token_symbol}* ({pct*100:.0f}%)\n\n"
                f"💵 Received: ${usd_received:.0f}\n"
                f"📊 PnL: ${pnl:+.0f} ({pnl_pct:+.1f}%)\n"
                f"📝 Reason: {reason}"
            )
            self.tracker.record_sell(token_address, usd_received, pnl, reason, pnl_pct=round(pnl_pct, 2))
            logger.info(f"{emoji} Sold {pct*100:.0f}% of {token_symbol} — PnL: ${pnl:+.0f}")

        except Exception as e:
            logger.error(f"Sell failed for {token_symbol}: {e}")
        finally:
            self._selling.discard(token_address)

    async def _monitor_positions(self):
        """Continuously monitor open positions for TP/SL triggers."""
        await asyncio.sleep(30)  # Wait for first positions to open
        while True:
            try:
                for token_address, position in list(self.open_positions.items()):
                    await self._check_position(position)
            except Exception as e:
                logger.error(f"Position monitor error: {e}")
            await asyncio.sleep(30)

    async def _check_position(self, position: Position):
        """Check if a position has hit take profit or stop loss."""
        current_price = await self._get_token_price(position.token_address)
        if current_price <= 0:
            return

        position.current_price_usd = current_price
        multiplier = current_price / position.entry_price_usd if position.entry_price_usd > 0 else 1
        position.pnl_usd = (multiplier - 1) * position.amount_usd

        # Stop loss
        if multiplier <= (1 - self.stop_loss_pct):
            logger.warning(f"🛑 Stop loss hit for {position.token_symbol}")
            await self.sell(position.token_address, position.token_symbol,
                          f"Stop loss at {(multiplier-1)*100:.1f}%", pct=1.0)
            return

        # Take profit 1 (2x) — sell 50%
        if multiplier >= self.tp1_multiplier and not position.take_profit_1_hit:
            position.take_profit_1_hit = True
            await self.sell(position.token_address, position.token_symbol,
                          f"TP1 at {multiplier:.1f}x", pct=0.50)

        # Take profit 2 (5x) — sell 30% of original (60% of remaining)
        elif multiplier >= self.tp2_multiplier and not position.take_profit_2_hit:
            position.take_profit_2_hit = True
            await self.sell(position.token_address, position.token_symbol,
                          f"TP2 at {multiplier:.1f}x", pct=0.60)

        # Take profit 3 (10x) — sell everything remaining
        elif multiplier >= self.tp3_multiplier:
            await self.sell(position.token_address, position.token_symbol,
                          f"TP3 at {multiplier:.1f}x", pct=1.0)

    async def _get_quote(self, input_mint: str, output_mint: str, amount: int) -> Optional[dict]:
        """Get a swap quote from Jupiter, with retries for transient DNS/network errors."""
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount,
            "slippageBps": 100  # 1% slippage
        }
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession(headers=_JUPITER_HEADERS) as session:
                    async with session.get(JUPITER_QUOTE_API, params=params,
                                           timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        logger.warning(f"Jupiter quote HTTP {resp.status} (attempt {attempt+1}/3)")
            except Exception as e:
                logger.warning(f"Jupiter quote error (attempt {attempt+1}/3): {e}")
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)  # 1s, 2s backoff
        return None

    async def _execute_swap(self, quote: dict) -> bool:
        """Execute a swap using Jupiter."""
        if not self.private_key:
            logger.warning("No private key set — skipping actual swap (paper trading mode)")
            return True  # Paper trading mode

        try:
            async with aiohttp.ClientSession(headers=_JUPITER_HEADERS) as session:
                payload = {
                    "quoteResponse": quote,
                    "userPublicKey": self._get_public_key(),
                    "wrapAndUnwrapSol": True,
                    "prioritizationFeeLamports": 10000
                }
                async with session.post(JUPITER_SWAP_API, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return False
                    swap_data = await resp.json()
                    swap_tx = swap_data.get("swapTransaction", "")
                    return await self._send_transaction(swap_tx)
        except Exception as e:
            logger.error(f"Swap execution error: {e}")
            return False

    async def _send_transaction(self, swap_tx_b64: str) -> bool:
        """Send a signed transaction to the Solana network."""
        try:
            from solders.keypair import Keypair
            from solders.transaction import VersionedTransaction
            import base58

            keypair = Keypair.from_base58_string(self.private_key)
            tx_bytes = base64.b64decode(swap_tx_b64)
            tx = VersionedTransaction.from_bytes(tx_bytes)
            tx.sign([keypair])

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    base64.b64encode(bytes(tx)).decode("utf-8"),
                    {"encoding": "base64", "skipPreflight": False}
                ]
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(self.rpc_url, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    result = await resp.json()
                    if "error" in result:
                        logger.error(f"TX error: {result['error']}")
                        return False
                    logger.info(f"TX sent: {result.get('result', '')}")
                    return True
        except ImportError:
            logger.warning("solders not installed — run: pip install solders")
            return False
        except Exception as e:
            logger.error(f"Transaction error: {e}")
            return False

    def _get_public_key(self) -> str:
        """Derive public key from private key."""
        try:
            from solders.keypair import Keypair
            keypair = Keypair.from_base58_string(self.private_key)
            return str(keypair.pubkey())
        except Exception:
            return ""

    async def _get_token_liquidity(self, token_address: str) -> float:
        """Get token pool liquidity in USD from DexScreener."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    data = await resp.json(content_type=None)
                    pairs = data.get("pairs", [])
                    if pairs:
                        return float(
                            pairs[0].get("liquidity", {}).get("usd", 0) or 0
                        )
        except Exception:
            pass
        return 50_000  # Fallback if unavailable

    async def _get_token_price(self, token_address: str) -> float:
        """Get current token price in USD — tries Axiom, Jupiter, then DexScreener."""
        # 1. Axiom token info (most reliable for tokens the bot trades)
        if self._axiom_auth is not None:
            try:
                client = self._axiom_auth.get_client()
                if client:
                    loop = asyncio.get_event_loop()
                    info = await loop.run_in_executor(None, client.get_token_info, token_address)
                    price = float(
                        info.get("priceUsd") or info.get("price_usd") or
                        info.get("price") or 0
                    )
                    if price > 0:
                        return price
            except Exception as e:
                logger.debug(f"[Trader] Axiom price lookup failed for {token_address[:8]}: {e}")
        # 2. Jupiter price API
        try:
            url = f"https://price.jup.ag/v4/price?ids={token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json()
                    price = data.get("data", {}).get(token_address, {}).get("price", 0)
                    if price and price > 0:
                        return float(price)
        except Exception:
            pass
        # DexScreener fallback (captures new pump.fun tokens not yet on Jupiter)
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json(content_type=None)
                    pairs = data.get("pairs") or []
                    if pairs:
                        price = float(pairs[0].get("priceUsd", 0) or 0)
                        if price > 0:
                            return price
        except Exception as e:
            logger.debug(f"[Trader] DexScreener price fallback failed for {token_address[:8]}: {e}")
        return 0

    async def _usd_to_sol(self, usd_amount: float) -> float:
        """Convert USD amount to SOL."""
        sol_price = await self._get_token_price(SOL_MINT)
        return usd_amount / sol_price if sol_price > 0 else 0

    async def _sol_to_usd(self, sol_amount: float) -> float:
        """Convert SOL amount to USD."""
        sol_price = await self._get_token_price(SOL_MINT)
        return sol_amount * sol_price
