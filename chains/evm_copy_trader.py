"""
EVM Copy Trader (Base + BNB)
Monitors block explorer APIs for wallet transactions
and delegates to EnhancedCopyTrader for all decision logic.
Uses proper ABI decoder for V2/V3/0x/1inch swap detection.
"""

import asyncio
import logging
import aiohttp
from typing import Dict, Set, Optional, List
from chains.chain_config import ChainConfig
from execution.evm_decoder import EVMTransactionDecoder
from core.enhanced_copy_trader import EnhancedCopyTrader

logger = logging.getLogger(__name__)


class EVMCopyTrader:
    def __init__(self, chain: ChainConfig, wallets: list,
                 trader, telegram, tracker,
                 kelly_sizer=None,
                 max_price_move_pct: float = 15.0,
                 min_hold_hours: float = 1.0,
                 max_hold_hours: float = 4.0,
                 min_win_rate: float = 0.50,
                 min_range_concentration: float = 0.50,
                 copy_delay_seconds: int = 5):

        self.chain = chain
        self.wallets = wallets
        self.trader = trader

        self.engine = EnhancedCopyTrader(
            chain_name=chain.name,
            chain_id=chain.chain_id,
            wallets=wallets,
            trader=trader,
            telegram=telegram,
            tracker=tracker,
            kelly_sizer=kelly_sizer,
            max_price_move_pct=max_price_move_pct,
            min_hold_hours=min_hold_hours,
            max_hold_hours=max_hold_hours,
            min_win_rate=min_win_rate,
            min_range_concentration=min_range_concentration,
            copy_delay_seconds=copy_delay_seconds
        )

        self.decoder = EVMTransactionDecoder(
            weth_address=chain.weth_address,
            usdc_address=chain.usdc_address
        )

        self._known_txns: Dict[str, Set[str]] = {w: set() for w in wallets}
        self._session: Optional[aiohttp.ClientSession] = None

        self.explorer_apis = {
            "base": "https://api.basescan.org/api",
            "bsc": "https://api.bscscan.com/api"
        }
        self.explorer_api_keys = {
            "base": "YOUR_BASESCAN_API_KEY",
            "bsc": "YOUR_BSCSCAN_API_KEY"
        }

    async def run(self):
        if not self.wallets:
            logger.info(f"[CopyTrader/{self.chain.name}] No wallets — skipping")
            return

        logger.info(
            f"[CopyTrader/{self.chain.name}] "
            f"Watching {len(self.wallets)} wallets"
        )
        async with aiohttp.ClientSession() as session:
            self._session = session
            await self._initialize_wallets()
            while True:
                for wallet in self.wallets:
                    try:
                        await self._check_wallet(wallet)
                    except Exception as e:
                        logger.error(
                            f"[CopyTrader/{self.chain.name}] {wallet[:8]}: {e}"
                        )
                await asyncio.sleep(15)

    async def _initialize_wallets(self):
        for wallet in self.wallets:
            txns = await self._get_transactions(wallet, limit=20)
            self._known_txns[wallet] = {t["hash"] for t in txns}

    async def _check_wallet(self, wallet: str):
        txns = await self._get_transactions(wallet, limit=10)
        for txn in txns:
            tx_hash = txn.get("hash", "")
            if tx_hash and tx_hash not in self._known_txns[wallet]:
                self._known_txns[wallet].add(tx_hash)
                await self._process_txn(wallet, txn)

    async def _process_txn(self, wallet: str, txn: dict):
        """Decode and process a transaction using proper ABI decoder."""
        input_data = txn.get("input", "")
        value_wei = int(txn.get("value", "0") or "0")

        if not self.decoder.is_swap(input_data):
            return

        decoded = self.decoder.decode(input_data, value_wei)
        if not decoded:
            return

        if decoded.action == "buy":
            token_address = decoded.token_out
            native_amount = value_wei / 1e18
            await self.engine.on_wallet_buy(
                wallet_address=wallet,
                token_address=token_address,
                token_symbol="?",
                wallet_entry_price=0,
                token_mcap=0
            )

        elif decoded.action == "sell":
            token_address = decoded.token_in
            await self.engine.on_wallet_sell(
                wallet_address=wallet,
                token_address=token_address,
                token_symbol="?"
            )

    async def _get_transactions(self, wallet: str,
                                 limit: int = 10) -> list:
        api_url = self.explorer_apis.get(self.chain.chain_id, "")
        api_key = self.explorer_api_keys.get(self.chain.chain_id, "")
        if not api_url:
            return []

        params = {
            "module": "account", "action": "txlist",
            "address": wallet, "page": 1,
            "offset": limit, "sort": "desc",
            "apikey": api_key
        }
        try:
            async with self._session.get(
                api_url, params=params,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                data = await r.json()
                return data.get("result", []) \
                    if data.get("status") == "1" else []
        except Exception:
            return []

    def get_stats(self) -> dict:
        return self.engine.get_stats()
