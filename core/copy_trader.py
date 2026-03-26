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

    def add_wallet(self, address: str):
        """Dynamically add a wallet to monitor (called from dashboard)."""
        if address in self.wallets:
            return
        self.wallets.append(address)
        self._known_sigs[address] = set()
        if address not in self.engine.profiles:
            from core.enhanced_copy_trader import WalletProfile
            label = address[:6] + "..." + address[-4:]
            self.engine.profiles[address] = WalletProfile(
                address=address, label=label, chain_id=self.engine.chain_id
            )
        logger.info(f"[CopyTrader/Solana] Added wallet {address[:8]}…")

    def remove_wallet(self, address: str):
        """Dynamically remove a wallet (called from dashboard)."""
        if address not in self.wallets:
            return
        self.wallets.remove(address)
        self._known_sigs.pop(address, None)
        self.engine.profiles.pop(address, None)
        logger.info(f"[CopyTrader/Solana] Removed wallet {address[:8]}…")

    async def run(self):
        logger.info(f"[CopyTrader/Solana] Started — watching {len(self.wallets)} wallets (dynamic)")
        async with aiohttp.ClientSession() as session:
            self._session = session
            await self._initialize_wallets()
            while True:
                if not self.wallets:
                    await asyncio.sleep(30)
                    continue
                for wallet in list(self.wallets):
                    try:
                        await self._check_wallet(wallet)
                    except Exception as e:
                        logger.error(f"[CopyTrader/Solana] {wallet[:8]}: {e}")
                await asyncio.sleep(30)  # was 10s — cuts Helius getSignaturesForAddress 3x

    async def _initialize_wallets(self):
        for wallet in list(self.wallets):
            if not self._known_sigs.get(wallet):
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
        ) as resp:
            data = await resp.json()
            return [item["signature"] for item in data.get("result", [])]

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
