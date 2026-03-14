"""
Solana Copy Trader
Monitors Helius WebSocket for wallet transactions
and delegates to EnhancedCopyTrader for all decision logic.
"""

import asyncio
import logging
import aiohttp
import json
from typing import Dict, Set, Optional, List
from core.enhanced_copy_trader import EnhancedCopyTrader

logger = logging.getLogger(__name__)

HELIUS_API = "https://api.helius.xyz/v0"


class CopyTrader:
    def __init__(self, wallets: list, trader, telegram, tracker,
                 kelly_sizer=None,
                 max_price_move_pct: float = 15.0,
                 min_hold_hours: float = 1.0,
                 max_hold_hours: float = 4.0,
                 min_win_rate: float = 0.50,
                 min_range_concentration: float = 0.50,
                 copy_delay_seconds: int = 5):

        self.wallets = wallets
        self.trader = trader
        self.telegram = telegram
        self.tracker = tracker
        self.rpc_url = trader.rpc_url

        self.engine = EnhancedCopyTrader(
            chain_name="Solana",
            chain_id="solana",
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

        self._known_sigs: Dict[str, Set[str]] = {w: set() for w in wallets}
        self._session: Optional[aiohttp.ClientSession] = None

    async def run(self):
        if not self.wallets:
            logger.info("[CopyTrader/Solana] No wallets — skipping")
            return

        logger.info(f"[CopyTrader/Solana] Watching {len(self.wallets)} wallets")
        async with aiohttp.ClientSession() as session:
            self._session = session
            await self._initialize_wallets()
            while True:
                for wallet in self.wallets:
                    try:
                        await self._check_wallet(wallet)
                    except Exception as e:
                        logger.error(f"[CopyTrader/Solana] {wallet[:8]}: {e}")
                await asyncio.sleep(10)

    async def _initialize_wallets(self):
        for wallet in self.wallets:
            sigs = await self._get_signatures(wallet, limit=20)
            self._known_sigs[wallet] = set(sigs)

    async def _check_wallet(self, wallet: str):
        sigs = await self._get_signatures(wallet, limit=10)
        new_sigs = [s for s in sigs if s not in self._known_sigs[wallet]]
        for sig in new_sigs:
            self._known_sigs[wallet].add(sig)
            await self._process_transaction(wallet, sig)

    async def _process_transaction(self, wallet: str, signature: str):
        tx = await self._get_transaction(signature)
        if not tx:
            return

        transfers = self._parse_swap(tx)
        for transfer in transfers:
            action = transfer.get("action")
            token_address = transfer.get("token", "")
            token_symbol = transfer.get("symbol", "?")
            amount_sol = transfer.get("amount_sol", 0)
            token_price = transfer.get("price_usd", 0)
            token_mcap = transfer.get("mcap", 0)

            if not token_address or amount_sol < 0.1:
                continue

            if action == "buy":
                await self.engine.on_wallet_buy(
                    wallet_address=wallet,
                    token_address=token_address,
                    token_symbol=token_symbol,
                    wallet_entry_price=token_price,
                    token_mcap=token_mcap
                )
            elif action == "sell":
                await self.engine.on_wallet_sell(
                    wallet_address=wallet,
                    token_address=token_address,
                    token_symbol=token_symbol
                )

    def _parse_swap(self, tx: dict) -> list:
        transfers = []
        try:
            events = tx.get("events", {})
            swap = events.get("swap", {})
            if not swap:
                return []

            native_input = swap.get("nativeInput")
            native_output = swap.get("nativeOutput")
            token_inputs = swap.get("tokenInputs", [])
            token_outputs = swap.get("tokenOutputs", [])

            if native_input and token_outputs:
                amount_sol = native_input.get("amount", 0) / 1e9
                for out in token_outputs:
                    transfers.append({
                        "action": "buy",
                        "token": out.get("mint", ""),
                        "symbol": out.get("symbol", "?"),
                        "amount_sol": amount_sol,
                        "price_usd": 0,
                        "mcap": 0
                    })

            elif native_output and token_inputs:
                amount_sol = native_output.get("amount", 0) / 1e9
                for inp in token_inputs:
                    transfers.append({
                        "action": "sell",
                        "token": inp.get("mint", ""),
                        "symbol": inp.get("symbol", "?"),
                        "amount_sol": amount_sol,
                        "price_usd": 0,
                        "mcap": 0
                    })
        except Exception as e:
            logger.debug(f"[CopyTrader/Solana] Parse error: {e}")
        return transfers

    async def _get_signatures(self, address: str, limit: int = 10) -> list:
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [address, {"limit": limit}]
        }
        async with self._session.post(
            self.rpc_url, json=payload,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            data = await r.json()
            return [r["signature"] for r in data.get("result", [])]

    async def _get_transaction(self, signature: str) -> Optional[dict]:
        api_key = self.rpc_url.split("api-key=")[-1] \
            if "api-key=" in self.rpc_url else ""
        url = f"{HELIUS_API}/transactions?api-key={api_key}"
        try:
            async with self._session.post(
                url, json={"transactions": [signature]},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                data = await r.json()
                return data[0] if data else None
        except Exception:
            return None

    def get_stats(self) -> dict:
        return self.engine.get_stats()
