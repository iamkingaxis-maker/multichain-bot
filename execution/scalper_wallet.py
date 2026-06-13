"""
Scalper Wallet Manager
Keeps scalp capital in a completely separate wallet from the main trader.
This prevents the scanner and scalper from competing for the same funds
and makes accounting clean.

The scalper wallet holds a small amount of SOL/ETH/BNB dedicated
purely to scalp trades. Profits auto-compound back into the scalp pool.
Losses are capped to the scalp wallet balance only.

Setup:
  1. Create a second wallet (Phantom for Solana, MetaMask for EVM)
  2. Fund it with your scalp allocation (e.g. $400 total)
  3. Add the private key to config.json as scalper_solana_private_key
     and scalper_evm_private_key
"""

import asyncio
import logging
import os
import aiohttp
from typing import Optional, Dict
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

JUPITER_QUOTE_API = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_API  = "https://quote-api.jup.ag/v6/swap"
SOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass
class ScalperWalletState:
    chain_id: str
    initial_capital_usd: float
    current_balance_usd: float = 0.0
    total_trades: int = 0
    total_pnl_usd: float = 0.0
    open_scalps: Dict[str, float] = field(default_factory=dict)  # token → usd_committed
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def available_usd(self) -> float:
        committed = sum(self.open_scalps.values())
        return max(0, self.current_balance_usd - committed)

    @property
    def roi_pct(self) -> float:
        if self.initial_capital_usd <= 0:
            return 0
        return (self.total_pnl_usd / self.initial_capital_usd) * 100


class ScalperWallet:
    """
    A dedicated wallet for scalp trades, completely isolated from main trading.
    Uses its own private key so funds are physically separate.
    """

    def __init__(self,
                 chain_id: str,
                 chain_name: str,
                 private_key: str,
                 rpc_url: str,
                 weth_address: str,
                 initial_capital_usd: float = 400.0,
                 max_per_scalp_usd: float = 50.0,
                 is_solana: bool = False):
        self.chain_id = chain_id
        self.chain_name = chain_name
        self.private_key = private_key
        self.rpc_url = rpc_url
        self.weth_address = weth_address
        self.max_per_scalp_usd = max_per_scalp_usd
        self.is_solana = is_solana
        self.paper_mode = not bool(private_key)

        # FAIL-CLOSED (2026-06-13 pre-live audit): ScalperWallet has its OWN key and
        # a raw Jupiter swap that bypasses the entire live-execution gate stack
        # (should_route_live + STRATEGY_ALLOWLIST + force_paper). It is currently
        # DEAD CODE — never instantiated; the live scalp path is scalp_queue ->
        # trader.buy(strategy="scalp", force_paper=True), which IS gated. Refuse to
        # construct with a REAL key unless an operator deliberately acks re-wiring it,
        # so it can never silently become an ungated real-money route if re-enabled.
        if private_key and os.environ.get("SCALPER_WALLET_LIVE_ACK", "").strip().lower() \
                not in ("1", "true", "yes"):
            raise RuntimeError(
                "ScalperWallet refuses a live key without SCALPER_WALLET_LIVE_ACK — it "
                "bypasses the should_route_live / STRATEGY_ALLOWLIST / force_paper gate "
                "stack. Route scalps through trader.buy(strategy='scalp', force_paper=True) "
                "instead, or set the ack only after adding an equivalent live gate here.")

        self.state = ScalperWalletState(
            chain_id=chain_id,
            initial_capital_usd=initial_capital_usd,
            current_balance_usd=initial_capital_usd
        )

        if self.paper_mode:
            logger.info(
                f"[ScalperWallet/{chain_name}] Paper mode — "
                f"${initial_capital_usd:.0f} simulated"
            )
        else:
            logger.info(
                f"[ScalperWallet/{chain_name}] Live mode — "
                f"${initial_capital_usd:.0f} allocated"
            )

    async def get_balance_usd(self) -> float:
        """Fetch actual wallet balance in USD."""
        if self.paper_mode:
            return self.state.current_balance_usd

        try:
            native_balance = await self._get_native_balance()
            native_price = await self._get_native_price()
            balance_usd = native_balance * native_price
            self.state.current_balance_usd = balance_usd
            return balance_usd
        except Exception as e:
            logger.error(f"[ScalperWallet/{self.chain_name}] Balance check failed: {e}")
            return self.state.current_balance_usd

    def can_scalp(self, usd_amount: float) -> bool:
        """Check if there's enough available balance for a scalp."""
        return self.state.available_usd >= usd_amount

    def get_scalp_size(self, requested_usd: float) -> float:
        """Return safe scalp size based on available balance."""
        available = self.state.available_usd
        return min(requested_usd, self.max_per_scalp_usd, available * 0.25)

    def commit_scalp(self, token_address: str, usd_amount: float):
        """Reserve funds for an active scalp."""
        self.state.open_scalps[token_address.lower()] = usd_amount
        logger.debug(
            f"[ScalperWallet/{self.chain_name}] Committed ${usd_amount:.0f} "
            f"for {token_address[:10]}... | Available: ${self.state.available_usd:.0f}"
        )

    def release_scalp(self, token_address: str, pnl_usd: float):
        """Release funds after a scalp closes."""
        committed = self.state.open_scalps.pop(token_address.lower(), 0)
        self.state.current_balance_usd += pnl_usd
        self.state.total_pnl_usd += pnl_usd
        self.state.total_trades += 1
        logger.info(
            f"[ScalperWallet/{self.chain_name}] "
            f"Scalp closed: PnL ${pnl_usd:+.2f} | "
            f"Balance: ${self.state.current_balance_usd:.0f} | "
            f"ROI: {self.state.roi_pct:+.1f}%"
        )

    async def execute_buy(self, token_address: str, token_symbol: str,
                          usd_amount: float) -> float:
        """
        Execute a scalp buy from this dedicated wallet.
        Returns amount of tokens bought (0 on failure).
        """
        if self.paper_mode:
            logger.info(
                f"[ScalperWallet/{self.chain_name}] [PAPER] "
                f"Buy ${usd_amount:.0f} of {token_symbol}"
            )
            # Simulate token amount
            price = await self._get_token_price(token_address)
            return usd_amount / price if price > 0 else 0

        if not self.can_scalp(usd_amount):
            logger.warning(
                f"[ScalperWallet/{self.chain_name}] "
                f"Insufficient balance for ${usd_amount:.0f} scalp"
            )
            return 0

        try:
            if self.is_solana:
                return await self._solana_buy(token_address, usd_amount)
            else:
                return await self._evm_buy(token_address, token_symbol, usd_amount)
        except Exception as e:
            logger.error(f"[ScalperWallet/{self.chain_name}] Buy failed: {e}")
            return 0

    async def execute_sell(self, token_address: str, token_symbol: str,
                            token_amount: float) -> float:
        """
        Execute a scalp sell from this wallet.
        Returns USD received.
        """
        if self.paper_mode:
            price = await self._get_token_price(token_address)
            usd_received = token_amount * price
            logger.info(
                f"[ScalperWallet/{self.chain_name}] [PAPER] "
                f"Sell {token_symbol} → ${usd_received:.0f}"
            )
            return usd_received

        try:
            if self.is_solana:
                return await self._solana_sell(token_address, token_amount)
            else:
                return await self._evm_sell(token_address, token_amount)
        except Exception as e:
            logger.error(f"[ScalperWallet/{self.chain_name}] Sell failed: {e}")
            return 0

    async def _solana_buy(self, token_address: str, usd_amount: float) -> float:
        """Execute buy on Solana via Jupiter from scalper wallet."""
        sol_price = await self._get_native_price()
        if sol_price <= 0:
            return 0

        sol_amount = usd_amount / sol_price
        lamports = int(sol_amount * 1e9)

        try:
            quote = await self._get_jupiter_quote(SOL_MINT, token_address, lamports)
            if not quote:
                return 0
            out_amount = int(quote.get("outAmount", 0))
            await self._execute_jupiter_swap(quote)
            return out_amount / 1e9
        except Exception as e:
            logger.error(f"Solana scalp buy error: {e}")
            return 0

    async def _solana_sell(self, token_address: str, amount: float) -> float:
        """Execute sell on Solana via Jupiter from scalper wallet."""
        sol_price = await self._get_native_price()
        try:
            lamports = int(amount * 1e9)
            quote = await self._get_jupiter_quote(token_address, SOL_MINT, lamports)
            if not quote:
                return 0
            sol_out = int(quote.get("outAmount", 0)) / 1e9
            await self._execute_jupiter_swap(quote)
            return sol_out * sol_price
        except Exception as e:
            logger.error(f"Solana scalp sell error: {e}")
            return 0

    async def _evm_buy(self, token_address: str, token_symbol: str,
                        usd_amount: float) -> float:
        """Execute buy on EVM chain via 0x from scalper wallet."""
        native_price = await self._get_native_price()
        if native_price <= 0:
            return 0

        native_amount = usd_amount / native_price
        wei_amount = int(native_amount * 1e18)

        try:
            quote = await self._get_0x_quote(
                self.weth_address, token_address, wei_amount
            )
            if not quote:
                return 0
            return int(quote.get("buyAmount", 0)) / 1e18
        except Exception as e:
            logger.error(f"EVM scalp buy error: {e}")
            return 0

    async def _evm_sell(self, token_address: str, amount: float) -> float:
        """Execute sell on EVM chain via 0x from scalper wallet."""
        native_price = await self._get_native_price()
        try:
            wei_amount = int(amount * 1e18)
            quote = await self._get_0x_quote(
                token_address, self.weth_address, wei_amount
            )
            if not quote:
                return 0
            native_out = int(quote.get("buyAmount", 0)) / 1e18
            return native_out * native_price
        except Exception as e:
            logger.error(f"EVM scalp sell error: {e}")
            return 0

    async def _get_jupiter_quote(self, input_mint: str, output_mint: str,
                                  amount: int) -> Optional[dict]:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount,
            "slippageBps": 100
        }
        async with aiohttp.ClientSession() as s:
            async with s.get(JUPITER_QUOTE_API, params=params,
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                return await r.json() if r.status == 200 else None

    async def _execute_jupiter_swap(self, quote: dict) -> bool:
        if not self.private_key:
            return True
        try:
            from solders.keypair import Keypair
            import base64
            kp = Keypair.from_base58_string(self.private_key)
            payload = {
                "quoteResponse": quote,
                "userPublicKey": str(kp.pubkey()),
                "wrapAndUnwrapSol": True
            }
            async with aiohttp.ClientSession() as s:
                async with s.post(JUPITER_SWAP_API, json=payload,
                                  timeout=aiohttp.ClientTimeout(total=15)) as r:
                    return r.status == 200
        except ImportError:
            return False

    async def _get_0x_quote(self, sell_token: str, buy_token: str,
                             amount: int) -> Optional[dict]:
        urls = {"base": "https://base.api.0x.org/swap/v1/quote",
                "bsc": "https://bsc.api.0x.org/swap/v1/quote"}
        url = urls.get(self.chain_id, "https://api.0x.org/swap/v1/quote")
        params = {"sellToken": sell_token, "buyToken": buy_token,
                  "sellAmount": amount, "slippagePercentage": 0.03}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params,
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
                return await r.json() if r.status == 200 else None

    async def _get_native_balance(self) -> float:
        if self.is_solana:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "getBalance",
                       "params": [self._get_pubkey()]}
            async with aiohttp.ClientSession() as s:
                async with s.post(self.rpc_url, json=payload,
                                  timeout=aiohttp.ClientTimeout(total=5)) as r:
                    data = await r.json()
                    return data.get("result", {}).get("value", 0) / 1e9
        else:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance",
                       "params": [self._get_pubkey(), "latest"]}
            async with aiohttp.ClientSession() as s:
                async with s.post(self.rpc_url, json=payload,
                                  timeout=aiohttp.ClientTimeout(total=5)) as r:
                    data = await r.json()
                    return int(data.get("result", "0x0"), 16) / 1e18

    def _get_pubkey(self) -> str:
        try:
            if self.is_solana:
                from solders.keypair import Keypair
                return str(Keypair.from_base58_string(self.private_key).pubkey())
            else:
                from web3 import Web3
                return Web3().eth.account.from_key(self.private_key).address
        except Exception:
            return ""

    async def _get_native_price(self) -> float:
        coin_ids = {"solana": "solana", "base": "ethereum", "bsc": "binancecoin"}
        coin = coin_ids.get(self.chain_id, "solana")
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin}&vs_currencies=usd"
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    data = await r.json()
                    return data.get(coin, {}).get("usd", 0)
        except Exception:
            return 0

    async def _get_token_price(self, token_address: str) -> float:
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    data = await r.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        return float(pairs[0].get("priceUsd", 0) or 0)
        except Exception:
            pass
        return 0

    def get_stats(self) -> dict:
        return {
            "chain": self.chain_name,
            "balance_usd": self.state.current_balance_usd,
            "available_usd": self.state.available_usd,
            "open_scalps": len(self.state.open_scalps),
            "total_trades": self.state.total_trades,
            "total_pnl_usd": self.state.total_pnl_usd,
            "roi_pct": self.state.roi_pct,
            "paper_mode": self.paper_mode
        }
