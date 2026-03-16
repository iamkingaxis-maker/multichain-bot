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
import os
import random
from typing import Dict, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, field

from core.paper_slippage import PaperSlippageSimulator

logger = logging.getLogger(__name__)

JUPITER_QUOTE_API = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_API = "https://quote-api.jup.ag/v6/swap"
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

JITO_BUNDLE_URL = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"
# Jito tip accounts (randomly chosen per tx to distribute tips)
JITO_TIP_ACCOUNTS = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
]


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
    entry_usd_value: float = 0.0   # position size in USD — used by PositionManager and tracker
    chain_id: str = "solana"        # chain identifier — used by tracker for chain breakdown


class Trader:
    def __init__(self, private_key: str, rpc_url: str, tracker, telegram, risk_manager,
                 tp1: float = 1.5, tp2: float = 2.0, tp3: float = 2.5, stop_loss: float = 0.20):
        self.private_key = private_key
        self.rpc_url = rpc_url
        self.tracker = tracker
        self.telegram = telegram
        self.risk_manager = risk_manager
        self.open_positions: Dict[str, Position] = {}
        self.session: Optional[aiohttp.ClientSession] = None

        # Take profit levels (from config): TP1=+50% (1.5x), TP2=+100% (2x), TP3=+150% (2.5x)
        self.tp1_multiplier = tp1
        self.tp2_multiplier = tp2
        self.tp3_multiplier = tp3
        self.stop_loss_pct = stop_loss

        # Paper trading slippage simulator
        self.paper_slippage = PaperSlippageSimulator("solana")

    async def buy(self, token_address: str, token_symbol: str,
                  reason: str, signal_score: int = 0,
                  hh_hl_confirmed: bool = False, price_hint: float = 0):
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
                    current_price = price_hint
                if current_price <= 0:
                    logger.warning(f"Skipping {token_symbol} — price unavailable on DexScreener")
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
                    entry_usd_value=position_size_usd,
                    chain_id="solana"
                )
                self.open_positions[token_address] = position
                self.risk_manager.record_buy(position_size_usd)
                self.tracker.save_position(position)

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
            sol_amount = await self._usd_to_sol(position_size_usd)
            if sol_amount <= 0:
                return

            quote = await self._swap_with_retry(
                input_mint=SOL_MINT,
                output_mint=token_address,
                amount=int(sol_amount * 1e9),
                symbol=token_symbol
            )
            if not quote:
                logger.error(f"Buy failed after retries: {token_symbol}")
                return

            out_amount = int(quote.get("outAmount", 0))
            entry_price = position_size_usd / (out_amount / 1e9) if out_amount > 0 else 0

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
                entry_usd_value=position_size_usd,
                chain_id="solana"
            )
            self.open_positions[token_address] = position
            self.risk_manager.record_buy(position_size_usd)
            self.tracker.save_position(position)

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

    def restore_positions(self):
        """Reload open positions from SQLite after a restart."""
        saved = self.tracker.get_open_positions("solana")
        if not saved:
            return
        restored = 0
        for data in saved:
            try:
                entry_time_raw = data.get("entry_time")
                entry_time = (
                    datetime.fromisoformat(entry_time_raw)
                    if isinstance(entry_time_raw, str)
                    else datetime.now(timezone.utc)
                )
                position = Position(
                    token_address=data["token_address"],
                    token_symbol=data["token_symbol"],
                    entry_price_usd=data["entry_price_usd"],
                    amount_tokens=data["amount_tokens"],
                    amount_sol_spent=data["amount_sol_spent"],
                    entry_time=entry_time,
                    reason=data.get("reason", "restored"),
                    take_profit_1_hit=data.get("take_profit_1_hit", False),
                    take_profit_2_hit=data.get("take_profit_2_hit", False),
                    signal_score=data.get("signal_score", 0),
                    hh_hl_confirmed=data.get("hh_hl_confirmed", False),
                    entry_usd_value=data.get("entry_usd_value", 0),
                    chain_id="solana"
                )
                self.open_positions[position.token_address] = position
                self.risk_manager.record_buy(position.entry_usd_value)
                restored += 1
            except Exception as e:
                logger.error(f"[Trader] restore error {data.get('token_address')}: {e}")
        if restored:
            logger.info(f"[Trader] ♻️ Restored {restored} open positions from DB")

    async def sell(self, token_address: str, token_symbol: str, reason: str, pct: float = 1.0):
        """Execute a sell order for a percentage of the position."""
        position = self.open_positions.get(token_address)
        if not position:
            logger.warning(f"No position found for {token_symbol}")
            return

        try:
            # ── PAPER TRADING MODE ────────────────────────────────────
            if not self.private_key:
                current_price = await self._get_token_price(token_address)
                if current_price <= 0:
                    # Fall back to last known price so the sell doesn't record $0
                    current_price = getattr(position, "current_price_usd", 0) or position.entry_price_usd
                if current_price <= 0:
                    logger.warning(f"Skipping sell {token_symbol} — price unavailable")
                    return

                # Sanity check: if sell price is > 20x last known, reject it.
                # This prevents a bad DexScreener reading from inflating paper PnL.
                prev = getattr(position, "current_price_usd", 0) or position.entry_price_usd
                if prev > 0 and current_price / prev > 20:
                    logger.warning(
                        f"⚠️ Sell price spike rejected for {token_symbol}: "
                        f"{prev:.8f} → {current_price:.8f} ({current_price/prev:.0f}x). "
                        f"Using last known price."
                    )
                    current_price = prev

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
                    self.tracker.delete_position(token_address, "solana")
                else:
                    position.amount_tokens *= (1 - pct)
                    position.amount_sol_spent *= (1 - pct)
                    position.entry_usd_value *= (1 - pct)
                    self.tracker.save_position(position)

                self.risk_manager.record_sell(usd_received, pnl)
                emoji = "🟢" if pnl >= 0 else "🔴"

                await self.telegram.send(
                    f"{emoji} *[PAPER] Sold ${token_symbol}* ({pct*100:.0f}%)\n\n"
                    f"💵 Received: ${usd_received:.2f}\n"
                    f"📊 PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
                    f"📉 Exit slippage: {slip_est.total_slippage_pct:.2f}%\n"
                    f"📝 {reason}"
                )
                self.tracker.record_sell(token_address, usd_received, pnl, reason)
                logger.info(
                    f"{emoji} [PAPER] Sold {pct*100:.0f}% of {token_symbol} — "
                    f"PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%) | {reason}"
                )
                return

            # ── LIVE TRADING MODE ─────────────────────────────────────
            tokens_to_sell = int(position.amount_tokens * pct * 1e9)

            quote = await self._sell_with_retry(
                input_mint=token_address,
                output_mint=SOL_MINT,
                amount=tokens_to_sell,
                symbol=token_symbol
            )

            sol_received = int(quote.get("outAmount", 0)) / 1e9
            usd_received = await self._sol_to_usd(sol_received)
            pnl = usd_received - (position.amount_sol_spent * pct * await self._sol_to_usd(1))
            pnl_pct = (pnl / (position.amount_sol_spent * pct * await self._sol_to_usd(1))) * 100

            if pct >= 1.0:
                del self.open_positions[token_address]
                self.tracker.delete_position(token_address, "solana")
            else:
                position.amount_tokens *= (1 - pct)
                position.amount_sol_spent *= (1 - pct)
                self.tracker.save_position(position)

            self.risk_manager.record_sell(usd_received, pnl)

            emoji = "🟢" if pnl >= 0 else "🔴"
            await self.telegram.send(
                f"{emoji} *Sold ${token_symbol}* ({pct*100:.0f}%)\n\n"
                f"💵 Received: ${usd_received:.0f}\n"
                f"📊 PnL: ${pnl:+.0f} ({pnl_pct:+.1f}%)\n"
                f"📝 Reason: {reason}"
            )
            self.tracker.record_sell(token_address, usd_received, pnl, reason)
            logger.info(f"{emoji} Sold {pct*100:.0f}% of {token_symbol} — PnL: ${pnl:+.0f}")

        except Exception as e:
            logger.error(f"Sell failed for {token_symbol}: {e}")

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

        # Sanity check: reject upward price spikes > 20x from last known price.
        # Pump.fun→Raydium pool migrations temporarily show absurd prices on DexScreener.
        prev = position.current_price_usd or position.entry_price_usd
        if prev > 0 and current_price / prev > 20:
            logger.warning(
                f"⚠️ Price spike rejected for {position.token_symbol}: "
                f"{prev:.8f} → {current_price:.8f} "
                f"({current_price/prev:.0f}x — likely pool migration artifact)"
            )
            return

        position.current_price_usd = current_price
        multiplier = current_price / position.entry_price_usd if position.entry_price_usd > 0 else 1
        position.pnl_usd = (multiplier - 1) * position.amount_sol_spent * await self._sol_to_usd(1)

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
        """Get a swap quote from Jupiter."""
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount,
            "slippageBps": 100  # 1% slippage
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(JUPITER_QUOTE_API, params=params,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None

    async def _execute_swap(self, quote: dict,
                             priority_fee: int = 10_000) -> bool:
        """Execute a swap using Jupiter."""
        if not self.private_key:
            logger.warning("No private key set — skipping actual swap (paper trading mode)")
            return True  # Paper trading mode

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "quoteResponse": quote,
                    "userPublicKey": self._get_public_key(),
                    "wrapAndUnwrapSol": True,
                    "prioritizationFeeLamports": priority_fee
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

    async def _swap_with_retry(self, input_mint: str, output_mint: str,
                                amount: int, symbol: str) -> Optional[dict]:
        """Get a quote and execute swap, retrying up to 3× with escalating priority fees."""
        priority_fees = [10_000, 50_000, 200_000]  # lamports: ~$0.001 / $0.005 / $0.02
        for attempt, fee in enumerate(priority_fees, 1):
            quote = await self._get_quote(input_mint, output_mint, amount)
            if not quote:
                logger.warning(f"[{symbol}] No quote on attempt {attempt}/3 — retrying")
                await asyncio.sleep(1)
                continue
            success = await self._execute_swap(quote, priority_fee=fee)
            if success:
                if attempt > 1:
                    logger.info(f"[{symbol}] Swap succeeded on attempt {attempt}/3 "
                                f"(priority fee: {fee:,} lamports)")
                return quote
            logger.warning(f"[{symbol}] Swap failed (attempt {attempt}/3, fee={fee:,}) — retrying")
            await asyncio.sleep(1.5)
        logger.error(f"[{symbol}] All 3 swap attempts failed")
        return None

    async def _sell_with_retry(self, input_mint: str, output_mint: str,
                                amount: int, symbol: str) -> Optional[dict]:
        """Sell with unlimited retries — keeps trying until the transaction lands.
        Priority fee escalates through 10k→50k→200k→500k lamports then holds at max.
        Used for all sells: stop-loss, TP, stall — position must be closed."""
        fee_schedule = [10_000, 50_000, 200_000, 500_000]
        attempt = 0
        while True:
            attempt += 1
            fee = fee_schedule[min(attempt - 1, len(fee_schedule) - 1)]
            quote = await self._get_quote(input_mint, output_mint, amount)
            if not quote:
                logger.warning(f"[{symbol}] Sell: no quote (attempt {attempt}, fee={fee:,}) — retrying in 2s")
                await asyncio.sleep(2)
                continue
            success = await self._execute_swap(quote, priority_fee=fee)
            if success:
                if attempt > 1:
                    logger.info(f"[{symbol}] Sell succeeded on attempt {attempt} "
                                f"(priority fee: {fee:,} lamports)")
                return quote
            logger.warning(f"[{symbol}] Sell failed (attempt {attempt}, fee={fee:,}) — retrying in 2s")
            await asyncio.sleep(2)

    async def _send_transaction(self, swap_tx_b64: str) -> bool:
        """Sign and send a transaction. Routes through Jito if JITO_TIP_LAMPORTS is set."""
        try:
            from solders.keypair import Keypair
            from solders.transaction import VersionedTransaction

            keypair = Keypair.from_base58_string(self.private_key)
            tx_bytes = base64.b64decode(swap_tx_b64)
            tx = VersionedTransaction.from_bytes(tx_bytes)
            tx.sign([keypair])
            signed_b64 = base64.b64encode(bytes(tx)).decode("utf-8")

            jito_tip = int(os.getenv("JITO_TIP_LAMPORTS", "0"))
            if jito_tip > 0:
                blockhash = str(tx.message.recent_blockhash)
                result = await self._send_jito_bundle(keypair, signed_b64, blockhash, jito_tip)
                if result:
                    return True
                logger.warning("Jito bundle failed — falling back to regular RPC")

            # Regular RPC submission (also used as Jito fallback)
            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "sendTransaction",
                "params": [signed_b64, {"encoding": "base64", "skipPreflight": False}]
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

    async def _send_jito_bundle(self, keypair, swap_tx_b64: str,
                                 blockhash: str, tip_lamports: int) -> bool:
        """Submit swap as a Jito private bundle — invisible to MEV bots until confirmed."""
        try:
            from solders.pubkey import Pubkey
            from solders.system_program import transfer, TransferParams
            from solders.message import MessageV0
            from solders.transaction import VersionedTransaction
            from solders.hash import Hash

            tip_account = Pubkey.from_string(random.choice(JITO_TIP_ACCOUNTS))
            bh = Hash.from_string(blockhash)

            # Build a tip transfer transaction using the same blockhash as the swap
            tip_ix = transfer(TransferParams(
                from_pubkey=keypair.pubkey(),
                to_pubkey=tip_account,
                lamports=tip_lamports
            ))
            msg = MessageV0.try_compile(
                payer=keypair.pubkey(),
                instructions=[tip_ix],
                address_lookup_table_accounts=[],
                recent_blockhash=bh
            )
            tip_tx = VersionedTransaction(msg, [keypair])
            tip_b64 = base64.b64encode(bytes(tip_tx)).decode("utf-8")

            # Bundle: [tip_tx, swap_tx] — atomic, private, MEV-protected
            bundle_payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "sendBundle",
                "params": [[tip_b64, swap_tx_b64]]
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(JITO_BUNDLE_URL, json=bundle_payload,
                                        timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    result = await resp.json()
                    if "error" in result:
                        logger.error(f"Jito bundle rejected: {result['error']}")
                        return False
                    bundle_id = result.get("result", "")
                    logger.info(f"🛡️ Jito bundle sent: {bundle_id[:20]}... "
                                f"(tip: {tip_lamports:,} lamports)")
                    return await self._confirm_jito_bundle(session, bundle_id)
        except Exception as e:
            logger.error(f"Jito bundle error: {e}")
            return False

    async def _confirm_jito_bundle(self, session, bundle_id: str,
                                    timeout_secs: int = 30) -> bool:
        """Poll Jito until the bundle confirms or times out."""
        for _ in range(timeout_secs // 2):
            await asyncio.sleep(2)
            try:
                async with session.post(JITO_BUNDLE_URL, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getBundleStatuses",
                    "params": [[bundle_id]]
                }, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    statuses = (data.get("result") or {}).get("value", [])
                    if not statuses:
                        continue
                    status = statuses[0].get("confirmation_status", "")
                    if status in ("confirmed", "finalized"):
                        logger.info(f"🛡️ Jito bundle confirmed ({status})")
                        return True
                    err = statuses[0].get("err")
                    if err:
                        logger.error(f"Jito bundle failed on-chain: {err}")
                        return False
            except Exception:
                pass
        logger.warning(f"Jito bundle timed out: {bundle_id[:20]}...")
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
                    data = await resp.json()
                    pairs = data.get("pairs") or []
                    if pairs:
                        return float(
                            pairs[0].get("liquidity", {}).get("usd", 0) or 0
                        )
        except Exception:
            pass
        return 50_000  # Fallback if unavailable

    async def _get_token_price(self, token_address: str) -> float:
        """Get current token price in USD from DexScreener."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return 0
                    data = await resp.json()
                    pairs = [
                        p for p in (data.get("pairs") or [])
                        if p.get("chainId") == "solana"
                    ]
                    if pairs:
                        best = max(pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0))
                        return float(best.get("priceUsd", 0) or 0)
        except Exception:
            pass
        return 0

    async def _get_sol_price(self) -> float:
        """Get SOL price in USD from CoinGecko."""
        try:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return 130.0
                    data = await resp.json()
                    return float(data.get("solana", {}).get("usd", 130.0))
        except Exception:
            return 130.0  # Fallback

    async def _usd_to_sol(self, usd_amount: float) -> float:
        """Convert USD amount to SOL."""
        sol_price = await self._get_sol_price()
        return usd_amount / sol_price if sol_price > 0 else 0

    async def _sol_to_usd(self, sol_amount: float) -> float:
        """Convert SOL amount to USD."""
        sol_price = await self._get_sol_price()
        return sol_amount * sol_price
