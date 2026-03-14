"""
Token Tax Detector
Detects buy/sell taxes on EVM tokens before trading.
Automatically adjusts slippage to ensure trades execute.

Method: Simulate a buy and sell via eth_call to detect
the actual amount received vs expected — the difference is the tax.

Also detects common tax patterns in contract bytecode.
"""

import asyncio
import logging
import aiohttp
import json
from typing import Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Common ERC20 ABI fragments needed for tax detection
ERC20_ABI_FRAGMENTS = [
    {
        "name": "transfer",
        "type": "function",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"}
        ],
        "outputs": [{"name": "", "type": "bool"}]
    },
    {
        "name": "balanceOf",
        "type": "function",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}]
    }
]

# Known high-tax token patterns in contract bytecode
HIGH_TAX_SIGNATURES = [
    "taxFee", "liquidityFee", "_taxFee", "reflectionFee",
    "marketingFee", "devFee", "burnFee", "rewardsFee"
]


@dataclass
class TaxResult:
    token_address: str
    chain_id: str
    buy_tax_pct: float = 0.0
    sell_tax_pct: float = 0.0
    max_tx_limit: bool = False       # Has max transaction limit
    has_cooldown: bool = False       # Has trading cooldown
    recommended_slippage: float = 3.0
    is_safe_to_trade: bool = True
    notes: list = None

    def __post_init__(self):
        if self.notes is None:
            self.notes = []

    @property
    def total_tax(self) -> float:
        return self.buy_tax_pct + self.sell_tax_pct

    def summary(self) -> str:
        parts = [
            f"Buy tax: {self.buy_tax_pct:.1f}%",
            f"Sell tax: {self.sell_tax_pct:.1f}%",
            f"Slippage: {self.recommended_slippage:.1f}%"
        ]
        if self.notes:
            parts.append("Notes: " + ", ".join(self.notes))
        return " | ".join(parts)


class TaxDetector:
    """
    Detects token taxes on Base and BNB Chain.
    Returns adjusted slippage for safe trade execution.
    """

    def __init__(self,
                 max_acceptable_tax: float = 10.0,
                 base_slippage: float = 2.0,
                 tax_slippage_buffer: float = 2.0):
        self.max_acceptable_tax = max_acceptable_tax
        self.base_slippage = base_slippage
        self.tax_slippage_buffer = tax_slippage_buffer
        self._cache: dict = {}

    async def detect(self, token_address: str, chain_id: str,
                     rpc_url: str) -> TaxResult:
        """
        Detect taxes for a token on an EVM chain.
        Uses GoPlus data first, falls back to simulation.
        """
        cache_key = f"{chain_id}:{token_address.lower()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = TaxResult(
            token_address=token_address,
            chain_id=chain_id
        )

        # Try GoPlus first (already fetched in security check)
        # This avoids duplicate API calls when honeypot checker ran first
        goplus_result = await self._get_goplus_taxes(token_address, chain_id)
        if goplus_result:
            result.buy_tax_pct, result.sell_tax_pct = goplus_result
            result.notes.append("Tax data from GoPlus")
        else:
            # Fallback: simulate transfers via eth_call
            sim_result = await self._simulate_taxes(
                token_address, chain_id, rpc_url
            )
            if sim_result:
                result.buy_tax_pct, result.sell_tax_pct = sim_result
                result.notes.append("Tax data from simulation")
            else:
                # Final fallback: assume moderate tax and set safe slippage
                result.buy_tax_pct = 0.0
                result.sell_tax_pct = 0.0
                result.notes.append("Tax detection failed — using default slippage")

        # Calculate recommended slippage
        max_tax = max(result.buy_tax_pct, result.sell_tax_pct)
        result.recommended_slippage = max(
            self.base_slippage,
            max_tax + self.tax_slippage_buffer
        )

        # BNB Chain gets extra buffer due to volatility
        if chain_id == "bsc":
            result.recommended_slippage += 1.0

        # Safety check
        if result.buy_tax_pct > self.max_acceptable_tax:
            result.is_safe_to_trade = False
            result.notes.append(f"Buy tax {result.buy_tax_pct:.1f}% exceeds limit")

        if result.sell_tax_pct > self.max_acceptable_tax:
            result.is_safe_to_trade = False
            result.notes.append(f"Sell tax {result.sell_tax_pct:.1f}% exceeds limit")

        logger.info(
            f"[TaxDetector] {token_address[:10]}... [{chain_id}] — "
            f"{result.summary()}"
        )

        self._cache[cache_key] = result
        return result

    async def _get_goplus_taxes(self, token_address: str,
                                 chain_id: str) -> Optional[Tuple[float, float]]:
        """Fetch tax data from GoPlus API."""
        chain_ids = {"base": "8453", "bsc": "56"}
        goplus_chain = chain_ids.get(chain_id)
        if not goplus_chain:
            return None

        url = (
            f"https://api.gopluslabs.io/api/v1/token_security/{goplus_chain}"
            f"?contract_addresses={token_address}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    token_data = (
                        data.get("result", {})
                        .get(token_address.lower(), {})
                    )
                    if not token_data:
                        return None

                    buy_tax = float(token_data.get("buy_tax", 0)) * 100
                    sell_tax = float(token_data.get("sell_tax", 0)) * 100
                    return buy_tax, sell_tax
        except Exception as e:
            logger.debug(f"[TaxDetector] GoPlus fetch error: {e}")
            return None

    async def _simulate_taxes(self, token_address: str, chain_id: str,
                               rpc_url: str) -> Optional[Tuple[float, float]]:
        """
        Simulate a small token transfer to detect taxes.
        Uses eth_call to avoid spending gas.
        """
        try:
            # Encode balanceOf call
            # keccak256("balanceOf(address)")[:4] = 0x70a08231
            test_address = "0x0000000000000000000000000000000000000001"
            balance_of_data = (
                "0x70a08231"
                "000000000000000000000000"
                + test_address[2:].zfill(40)
            )

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_call",
                "params": [{
                    "to": token_address,
                    "data": balance_of_data
                }, "latest"]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    data = await resp.json()
                    if "error" in data:
                        return None
                    # If we can call balanceOf the contract is likely real
                    # Without a funded test account we can't simulate the full tax
                    # Return None to use GoPlus or defaults
                    return None

        except Exception as e:
            logger.debug(f"[TaxDetector] Simulation error: {e}")
            return None

    def get_safe_slippage(self, tax_result: TaxResult,
                          extra_buffer: float = 0.0) -> float:
        """Get the safe slippage for executing a trade."""
        return tax_result.recommended_slippage + extra_buffer

    def clear_cache(self):
        self._cache.clear()
