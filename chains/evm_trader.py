"""
EVM Trader
Handles buy/sell execution on EVM chains (Base and BNB)
using the 0x Protocol swap aggregator and web3.py.
"""

import asyncio
import logging
import aiohttp
from typing import Dict, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, field
from chains.chain_config import ChainConfig

from core.paper_slippage import PaperSlippageSimulator
logger = logging.getLogger(__name__)

ZEROX_BASE_URL = "https://api.0x.org/swap/v1/quote"
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"


@dataclass
class EVMPosition:
    token_address: str
    token_symbol: str
    entry_price_usd: float
    amount_tokens: float
    amount_native_spent: float   # ETH or BNB
    entry_usd_value: float
    entry_time: datetime
    reason: str
    chain_id: str
    take_profit_1_hit: bool = False
    take_profit_2_hit: bool = False
    current_price_usd: float = 0.0
    pnl_usd: float = 0.0
    # Signal quality at entry — used by PositionManager for pyramid decisions
    signal_score: int = 0
    hh_hl_confirmed: bool = False


class EVMTrader:
    def __init__(self, chain: ChainConfig, private_key: str,
                 tracker, telegram, risk_manager,
                 tp1: float = 2.0, tp2: float = 5.0,
                 tp3: float = 10.0, stop_loss: float = 0.30):
        self.chain = chain
        self.private_key = private_key
        self.tracker = tracker
        self.telegram = telegram
        self.risk_manager = risk_manager
        self.rpc_url = chain.rpc_url
        self.open_positions: Dict[str, EVMPosition] = {}

        self.tp1_multiplier = tp1
        self.tp2_multiplier = tp2
        self.tp3_multiplier = tp3
        self.stop_loss_pct = stop_loss

        # Paper trading slippage simulator
        self.paper_slippage = PaperSlippageSimulator(chain.chain_id)

        asyncio.create_task(self._monitor_positions())

    async def buy(self, token_address: str, token_symbol: str,
                  reason: str, signal_score: int = 0,
                  hh_hl_confirmed: bool = False):
        """Execute a buy on an EVM chain."""
        position_size_usd = self.risk_manager.get_position_size()
        if position_size_usd <= 0:
            return

        # ── PAPER TRADING MODE ────────────────────────────────────────
        if not self.private_key:
            current_price = await self._get_token_price_usd(token_address)
            liquidity_usd = await self._get_token_liquidity(token_address)
            native_price = await self._get_native_price()
            native_amount = position_size_usd / native_price if native_price > 0 else 0

            adj_price, tokens_received, slip_est =                 self.paper_slippage.apply_to_buy(
                    position_size_usd, liquidity_usd,
                    current_price, token_symbol
                )

            from dataclasses import dataclass
            position = EVMPosition(
                token_address=token_address.lower(),
                token_symbol=token_symbol,
                entry_price_usd=adj_price,
                amount_tokens=tokens_received,
                amount_native_spent=native_amount,
                entry_usd_value=position_size_usd,
                entry_time=datetime.now(timezone.utc),
                reason=reason,
                chain_id=self.chain.chain_id,
                signal_score=signal_score,
                hh_hl_confirmed=hh_hl_confirmed
            )
            self.open_positions[token_address.lower()] = position
            self.risk_manager.record_buy(position_size_usd)
            await self.telegram.send(
                f"📄 *[PAPER] Bought ${token_symbol}* [{self.chain.name}]\n\n"
                f"💵 Size: ${position_size_usd:.0f}\n"
                f"💰 Entry: ${adj_price:.8f} "
                f"(+{slip_est.total_slippage_pct:.2f}% slippage)\n"
                f"🪙 Tokens: {tokens_received:.4f}\n"
                f"📝 {reason}"
            )
            self.tracker.record_buy(position)
            logger.info(
                f"📄 [PAPER] Bought {token_symbol} [{self.chain.name}] — "
                f"${position_size_usd:.0f} | Slippage: {slip_est.total_slippage_pct:.2f}%"
            )
            return

        logger.info(f"[{self.chain.name}] 💚 Buying {token_symbol} — ${position_size_usd:.0f}")

        try:
            native_price = await self._get_native_price()
            if native_price <= 0:
                return
            native_amount = position_size_usd / native_price

            # Get 0x quote
            quote = await self._get_0x_quote(
                sell_token=self.chain.weth_address,
                buy_token=token_address,
                sell_amount=int(native_amount * 1e18)
            )
            if not quote:
                logger.error(f"No quote for {token_symbol} on {self.chain.name}")
                return

            buy_amount = int(quote.get("buyAmount", 0))
            entry_price = position_size_usd / (buy_amount / 1e18) if buy_amount > 0 else 0

            success = await self._execute_evm_swap(quote, token_address)
            if not success:
                return

            position = EVMPosition(
                token_address=token_address.lower(),
                token_symbol=token_symbol,
                entry_price_usd=entry_price,
                amount_tokens=buy_amount / 1e18,
                amount_native_spent=native_amount,
                entry_usd_value=position_size_usd,
                entry_time=datetime.now(timezone.utc),
                reason=reason,
                chain_id=self.chain.chain_id,
                signal_score=signal_score,
                hh_hl_confirmed=hh_hl_confirmed
            )
            self.open_positions[token_address.lower()] = position
            self.risk_manager.record_buy(position_size_usd)

            await self.telegram.send(
                f"✅ *Bought ${token_symbol}* [{self.chain.name}]\n\n"
                f"💵 Size: ${position_size_usd:.0f}\n"
                f"📝 Reason: {reason}\n"
                f"🎯 TP1: {self.tp1_multiplier}x | TP2: {self.tp2_multiplier}x | TP3: {self.tp3_multiplier}x\n"
                f"🛑 Stop: -{self.stop_loss_pct*100:.0f}%"
            )
            self.tracker.record_buy(position)

        except Exception as e:
            logger.error(f"[{self.chain.name}] Buy failed for {token_symbol}: {e}")

    async def sell(self, token_address: str, token_symbol: str,
                   reason: str, pct: float = 1.0):
        """Execute a sell on an EVM chain."""
        position = self.open_positions.get(token_address.lower())
        if not position:
            return

        try:
            tokens_to_sell = int(position.amount_tokens * pct * 1e18)
            quote = await self._get_0x_quote(
                sell_token=token_address,
                buy_token=self.chain.weth_address,
                sell_amount=tokens_to_sell
            )
            if not quote:
                return

            native_received = int(quote.get("buyAmount", 0)) / 1e18
            native_price = await self._get_native_price()
            usd_received = native_received * native_price
            cost_basis = position.entry_usd_value * pct
            pnl = usd_received - cost_basis
            pnl_pct = (pnl / cost_basis) * 100 if cost_basis > 0 else 0

            success = await self._execute_evm_swap(quote, self.chain.weth_address)
            if not success:
                return

            if pct >= 1.0:
                del self.open_positions[token_address.lower()]
            else:
                position.amount_tokens *= (1 - pct)
                position.entry_usd_value *= (1 - pct)

            self.risk_manager.record_sell(usd_received, pnl)

            emoji = "🟢" if pnl >= 0 else "🔴"
            await self.telegram.send(
                f"{emoji} *Sold ${token_symbol}* [{self.chain.name}] ({pct*100:.0f}%)\n\n"
                f"💵 Received: ${usd_received:.0f}\n"
                f"📊 PnL: ${pnl:+.0f} ({pnl_pct:+.1f}%)\n"
                f"📝 Reason: {reason}"
            )
            self.tracker.record_sell(token_address, usd_received, pnl, reason)

        except Exception as e:
            logger.error(f"[{self.chain.name}] Sell failed for {token_symbol}: {e}")

    async def _monitor_positions(self):
        """Check positions for TP/SL every 30 seconds."""
        await asyncio.sleep(30)
        while True:
            try:
                for addr, position in list(self.open_positions.items()):
                    await self._check_position(position)
            except Exception as e:
                logger.error(f"[{self.chain.name}] Position monitor error: {e}")
            await asyncio.sleep(30)

    async def _check_position(self, position: EVMPosition):
        """Check TP/SL for a single position."""
        current_price = await self._get_token_price_usd(position.token_address)
        if current_price <= 0:
            return

        position.current_price_usd = current_price
        multiplier = current_price / position.entry_price_usd if position.entry_price_usd > 0 else 1
        position.pnl_usd = (multiplier - 1) * position.entry_usd_value

        # Stop loss
        if multiplier <= (1 - self.stop_loss_pct):
            await self.sell(position.token_address, position.token_symbol,
                           f"Stop loss at {(multiplier-1)*100:.1f}%", pct=1.0)
            return

        # Take profit tiers
        if multiplier >= self.tp1_multiplier and not position.take_profit_1_hit:
            position.take_profit_1_hit = True
            await self.sell(position.token_address, position.token_symbol,
                           f"TP1 at {multiplier:.1f}x", pct=0.50)
        elif multiplier >= self.tp2_multiplier and not position.take_profit_2_hit:
            position.take_profit_2_hit = True
            await self.sell(position.token_address, position.token_symbol,
                           f"TP2 at {multiplier:.1f}x", pct=0.60)
        elif multiplier >= self.tp3_multiplier:
            await self.sell(position.token_address, position.token_symbol,
                           f"TP3 at {multiplier:.1f}x", pct=1.0)

    async def _get_0x_quote(self, sell_token: str, buy_token: str,
                             sell_amount: int) -> Optional[dict]:
        """Get a swap quote from 0x Protocol."""
        # Chain-specific 0x endpoints
        base_urls = {
            "base": "https://base.api.0x.org/swap/v1/quote",
            "bsc": "https://bsc.api.0x.org/swap/v1/quote"
        }
        url = base_urls.get(self.chain.chain_id, ZEROX_BASE_URL)
        params = {
            "sellToken": sell_token,
            "buyToken": buy_token,
            "sellAmount": sell_amount,
            "slippagePercentage": 0.02  # 2% slippage for memecoins
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params,
                                       timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    error = await resp.text()
                    logger.error(f"0x quote error {resp.status}: {error[:200]}")
                    return None
        except Exception as e:
            logger.error(f"0x quote exception: {e}")
            return None

    async def _execute_evm_swap(self, quote: dict, output_token: str) -> bool:
        """Execute an EVM swap using web3.py."""
        if not self.private_key:
            logger.warning(f"[{self.chain.name}] No private key — paper trading mode")
            return True

        try:
            from web3 import Web3
            from web3.middleware import geth_poa_middleware

            w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            if self.chain.chain_id == "bsc":
                w3.middleware_onion.inject(geth_poa_middleware, layer=0)

            account = w3.eth.account.from_key(self.private_key)
            to = quote.get("to", "")
            data = quote.get("data", "")
            value = int(quote.get("value", 0))
            gas_price = w3.eth.gas_price

            # Approve token spend if selling a token (not native)
            sell_token = quote.get("sellTokenAddress", "")
            if sell_token.lower() != self.chain.weth_address.lower():
                await self._approve_token(w3, account, sell_token, to)

            tx = {
                "from": account.address,
                "to": w3.to_checksum_address(to),
                "data": data,
                "value": value,
                "gas": 300000,
                "gasPrice": gas_price,
                "nonce": w3.eth.get_transaction_count(account.address),
                "chainId": w3.eth.chain_id
            }

            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

            if receipt.status == 1:
                logger.info(f"[{self.chain.name}] TX success: {tx_hash.hex()}")
                return True
            else:
                logger.error(f"[{self.chain.name}] TX failed: {tx_hash.hex()}")
                return False

        except ImportError:
            logger.warning("web3 not installed — run: pip install web3")
            return False
        except Exception as e:
            logger.error(f"[{self.chain.name}] EVM swap error: {e}")
            return False

    async def _approve_token(self, w3, account, token_address: str, spender: str):
        """Approve a token for spending by the swap router."""
        try:
            erc20_abi = [{"constant": False, "inputs": [
                {"name": "_spender", "type": "address"},
                {"name": "_value", "type": "uint256"}
            ], "name": "approve", "outputs": [{"name": "", "type": "bool"}],
              "type": "function"}]

            contract = w3.eth.contract(
                address=w3.to_checksum_address(token_address),
                abi=erc20_abi
            )
            max_amount = 2**256 - 1
            tx = contract.functions.approve(
                w3.to_checksum_address(spender), max_amount
            ).build_transaction({
                "from": account.address,
                "gas": 100000,
                "gasPrice": w3.eth.gas_price,
                "nonce": w3.eth.get_transaction_count(account.address),
                "chainId": w3.eth.chain_id
            })
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            logger.info(f"[{self.chain.name}] Token approved: {token_address[:10]}...")
        except Exception as e:
            logger.error(f"[{self.chain.name}] Approval failed: {e}")

    async def _get_token_liquidity(self, token_address: str) -> float:
        """Get token liquidity from DexScreener."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    data = await resp.json()
                    pairs = [
                        p for p in data.get("pairs", [])
                        if p.get("chainId") == self.chain.chain_id
                    ]
                    if pairs:
                        return float(
                            pairs[0].get("liquidity", {}).get("usd", 0) or 0
                        )
        except Exception:
            pass
        return 50_000

    async def _get_native_price(self) -> float:
        """Get the native token price in USD from CoinGecko."""
        try:
            url = f"{COINGECKO_PRICE_URL}?ids={self.chain.native_token_coingecko_id}&vs_currencies=usd"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json()
                    return data.get(self.chain.native_token_coingecko_id, {}).get("usd", 0)
        except Exception:
            return 0

    async def _get_token_price_usd(self, token_address: str) -> float:
        """Get token price in USD via DexScreener."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        return float(pairs[0].get("priceUsd", 0))
                    return 0
        except Exception:
            return 0
