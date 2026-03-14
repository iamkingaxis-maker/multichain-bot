"""
Gas Price Oracle
Monitors real-time gas prices on Base and BNB Chain.
Automatically adjusts transaction gas to ensure trades execute
without overpaying during normal conditions or failing during spikes.

Strategy:
  - Fetch current gas price from RPC
  - Apply multiplier based on urgency (scalper needs faster confirmation)
  - Cap at max_gwei to prevent runaway costs
  - Alert if gas is abnormally high
"""

import asyncio
import logging
import aiohttp
from typing import Dict, Optional
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Gas price APIs
ETHERSCAN_GAS_API = "https://api.etherscan.io/api?module=gastracker&action=gasoracle"
BSCSCAN_GAS_API   = "https://api.bscscan.com/api?module=gastracker&action=gasoracle"

# Default max gas prices (Gwei) — above this we delay or skip
DEFAULT_MAX_GWEI = {
    "base": 0.5,      # Base is very cheap — anything over 0.5 gwei is unusual
    "bsc":  5.0,      # BNB Chain standard is ~1-3 gwei
    "solana": None    # Solana uses priority fees differently
}

# Gas limits per transaction type
GAS_LIMITS = {
    "erc20_approve": 65_000,
    "v2_swap":       200_000,
    "v3_swap":       250_000,
    "complex_swap":  400_000,
}


@dataclass
class GasEstimate:
    chain_id: str
    safe_gwei: float        # Slow (minutes)
    standard_gwei: float    # Normal (30s-1min)
    fast_gwei: float        # Fast (< 30s)
    instant_gwei: float     # Near-instant
    is_congested: bool
    last_updated: datetime

    def for_urgency(self, urgency: str = "standard") -> float:
        """Return gas price for the given urgency level."""
        return {
            "safe": self.safe_gwei,
            "standard": self.standard_gwei,
            "fast": self.fast_gwei,
            "instant": self.instant_gwei
        }.get(urgency, self.standard_gwei)

    def to_wei(self, urgency: str = "standard") -> int:
        return int(self.for_urgency(urgency) * 1e9)

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.last_updated).total_seconds()


class GasOracle:
    """
    Real-time gas price oracle for EVM chains.
    The scalper uses "fast" urgency. The scanner uses "standard".
    """

    def __init__(self,
                 max_gwei_override: Optional[Dict[str, float]] = None,
                 alert_on_congestion: bool = True,
                 cache_ttl_seconds: int = 15):
        self.max_gwei = {**DEFAULT_MAX_GWEI, **(max_gwei_override or {})}
        self.alert_on_congestion = alert_on_congestion
        self.cache_ttl = cache_ttl_seconds
        self._cache: Dict[str, GasEstimate] = {}
        self._congestion_alerted: Dict[str, bool] = {}

    async def get_gas(self, chain_id: str,
                      rpc_url: str) -> GasEstimate:
        """
        Get current gas estimate for a chain.
        Returns cached result if fresh enough.
        """
        cached = self._cache.get(chain_id)
        if cached and cached.age_seconds < self.cache_ttl:
            return cached

        estimate = await self._fetch_gas(chain_id, rpc_url)
        self._cache[chain_id] = estimate

        # Log and alert on congestion
        if estimate.is_congested and not self._congestion_alerted.get(chain_id):
            logger.warning(
                f"[GasOracle] ⚠️ {chain_id.upper()} CONGESTED — "
                f"standard gas: {estimate.standard_gwei:.2f} gwei"
            )
            self._congestion_alerted[chain_id] = True
        elif not estimate.is_congested:
            self._congestion_alerted[chain_id] = False

        return estimate

    async def get_tx_params(self, chain_id: str, rpc_url: str,
                             urgency: str = "standard",
                             gas_limit: int = 250_000) -> dict:
        """
        Get complete transaction parameters including gas price and limit.
        Pass directly into web3 transaction dict.
        """
        estimate = await self.get_gas(chain_id, rpc_url)
        gas_gwei = estimate.for_urgency(urgency)
        max_gas = self.max_gwei.get(chain_id, 10.0)

        if gas_gwei > max_gas:
            logger.warning(
                f"[GasOracle] {chain_id} gas {gas_gwei:.2f} gwei exceeds "
                f"max {max_gas:.2f} gwei — capping"
            )
            gas_gwei = max_gas

        return {
            "gas": gas_limit,
            "gasPrice": int(gas_gwei * 1e9)
        }

    def is_acceptable(self, chain_id: str) -> bool:
        """Return True if current gas is within acceptable range."""
        cached = self._cache.get(chain_id)
        if not cached:
            return True  # Unknown — allow
        max_gas = self.max_gwei.get(chain_id, 10.0)
        if max_gas is None:
            return True
        return cached.standard_gwei <= max_gas * 1.5

    async def _fetch_gas(self, chain_id: str, rpc_url: str) -> GasEstimate:
        """Fetch current gas prices from RPC and gas tracker APIs."""
        try:
            # Primary: fetch from RPC
            rpc_gas = await self._fetch_rpc_gas(rpc_url)

            # Secondary: fetch from gas tracker API for more granularity
            tracker_data = await self._fetch_tracker_gas(chain_id)

            if tracker_data:
                safe = tracker_data.get("safe", rpc_gas * 0.8)
                standard = tracker_data.get("standard", rpc_gas)
                fast = tracker_data.get("fast", rpc_gas * 1.2)
                instant = tracker_data.get("instant", rpc_gas * 1.5)
            else:
                # Estimate tiers from RPC base gas
                safe = rpc_gas * 0.85
                standard = rpc_gas
                fast = rpc_gas * 1.15
                instant = rpc_gas * 1.35

            max_gas = self.max_gwei.get(chain_id, 10.0)
            is_congested = (
                max_gas is not None and
                standard > max_gas * 1.2
            )

            estimate = GasEstimate(
                chain_id=chain_id,
                safe_gwei=round(safe, 4),
                standard_gwei=round(standard, 4),
                fast_gwei=round(fast, 4),
                instant_gwei=round(instant, 4),
                is_congested=is_congested,
                last_updated=datetime.now(timezone.utc)
            )

            logger.debug(
                f"[GasOracle] {chain_id}: "
                f"safe={estimate.safe_gwei:.3f} | "
                f"std={estimate.standard_gwei:.3f} | "
                f"fast={estimate.fast_gwei:.3f} gwei"
            )
            return estimate

        except Exception as e:
            logger.error(f"[GasOracle] Failed to fetch gas for {chain_id}: {e}")
            return self._fallback_estimate(chain_id)

    async def _fetch_rpc_gas(self, rpc_url: str) -> float:
        """Fetch gas price from RPC eth_gasPrice."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_gasPrice",
            "params": []
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(
                rpc_url, json=payload,
                timeout=aiohttp.ClientTimeout(total=5)
            ) as r:
                data = await r.json()
                hex_price = data.get("result", "0x0")
                wei_price = int(hex_price, 16)
                return wei_price / 1e9  # Convert to Gwei

    async def _fetch_tracker_gas(self, chain_id: str) -> Optional[dict]:
        """Fetch gas tiers from block explorer gas tracker."""
        apis = {
            "base": ETHERSCAN_GAS_API,  # Base uses Etherscan-compatible API
            "bsc": BSCSCAN_GAS_API
        }
        url = apis.get(chain_id)
        if not url:
            return None

        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    data = await r.json()
                    result = data.get("result", {})
                    return {
                        "safe": float(result.get("SafeGasPrice", 0)),
                        "standard": float(result.get("ProposeGasPrice", 0)),
                        "fast": float(result.get("FastGasPrice", 0)),
                        "instant": float(result.get("FastGasPrice", 0)) * 1.2
                    }
        except Exception:
            return None

    def _fallback_estimate(self, chain_id: str) -> GasEstimate:
        """Safe fallback when gas fetch fails."""
        defaults = {"base": 0.1, "bsc": 3.0}
        base = defaults.get(chain_id, 5.0)
        return GasEstimate(
            chain_id=chain_id,
            safe_gwei=base * 0.8,
            standard_gwei=base,
            fast_gwei=base * 1.2,
            instant_gwei=base * 1.5,
            is_congested=False,
            last_updated=datetime.now(timezone.utc)
        )

    def get_stats(self) -> dict:
        stats = {}
        for chain_id, estimate in self._cache.items():
            stats[chain_id] = {
                "standard_gwei": estimate.standard_gwei,
                "fast_gwei": estimate.fast_gwei,
                "is_congested": estimate.is_congested,
                "age_seconds": round(estimate.age_seconds, 1)
            }
        return stats
