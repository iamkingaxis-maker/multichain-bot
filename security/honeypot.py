"""
Honeypot & Token Security Checker
Runs every token through multiple security checks before any buy.
Uses GoPlus Security API + on-chain analysis.

Checks performed:
  - Honeypot detection (can you actually sell?)
  - Buy/sell tax detection
  - Mint authority (can dev print more tokens?)
  - Freeze authority (can dev freeze wallets?)
  - Blacklist function (can dev block sellers?)
  - Proxy contract (hidden logic?)
  - Top holder concentration
  - Dev wallet holdings
  - Liquidity lock status
"""

import asyncio
import logging
import aiohttp
from dataclasses import dataclass, field
from typing import Optional, Dict
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

GOPLUS_API = "https://api.gopluslabs.io/api/v1"

# GoPlus chain IDs
GOPLUS_CHAIN_IDS = {
    "solana": "solana",
    "base":   "8453",
    "bsc":    "56"
}


@dataclass
class SecurityResult:
    token_address: str
    chain_id: str
    passed: bool
    risk_level: str          # "SAFE", "CAUTION", "DANGER", "BLOCK"
    honeypot: bool = False
    buy_tax: float = 0.0
    sell_tax: float = 0.0
    can_mint: bool = False
    can_freeze: bool = False
    has_blacklist: bool = False
    is_proxy: bool = False
    top10_concentration: float = 0.0
    dev_holding_pct: float = 0.0
    liquidity_locked: bool = False
    flags: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def summary(self) -> str:
        lines = [f"🔒 Security: {self.risk_level}"]
        if self.flags:
            lines.append("🚩 Flags: " + " | ".join(self.flags))
        if self.warnings:
            lines.append("⚠️ Warnings: " + " | ".join(self.warnings))
        lines.append(f"Buy tax: {self.buy_tax:.1f}% | Sell tax: {self.sell_tax:.1f}%")
        lines.append(f"Top10 concentration: {self.top10_concentration:.1f}%")
        return "\n".join(lines)


class SecurityChecker:
    """
    Pre-trade security gate. Call check() before any buy.
    Returns SecurityResult — if result.passed is False, skip the trade.
    """

    def __init__(self,
                 max_buy_tax: float = 10.0,
                 max_sell_tax: float = 10.0,
                 max_top10_concentration: float = 80.0,
                 max_dev_holding_pct: float = 15.0,
                 block_mintable: bool = True,
                 block_honeypot: bool = True,
                 block_blacklist: bool = True,
                 cache_ttl_seconds: int = 300):
        self.max_buy_tax = max_buy_tax
        self.max_sell_tax = max_sell_tax
        self.max_top10_concentration = max_top10_concentration
        self.max_dev_holding_pct = max_dev_holding_pct
        self.block_mintable = block_mintable
        self.block_honeypot = block_honeypot
        self.block_blacklist = block_blacklist
        self.cache_ttl = cache_ttl_seconds

        # Cache results to avoid re-checking same token
        self._cache: Dict[str, SecurityResult] = {}
        self._check_count = 0
        self._block_count = 0

    async def check(self, token_address: str, chain_id: str,
                    token_symbol: str = "?") -> SecurityResult:
        """
        Main entry point. Run all security checks on a token.
        Returns SecurityResult with passed=True only if safe to trade.
        """
        cache_key = f"{chain_id}:{token_address.lower()}"

        # Return cached result if still fresh
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            age = (datetime.now(timezone.utc) - cached.checked_at).total_seconds()
            if age < self.cache_ttl:
                return cached

        self._check_count += 1
        logger.info(f"🔍 Security check #{self._check_count}: {token_symbol} on {chain_id}")

        result = await self._run_checks(token_address, chain_id)
        self._cache[cache_key] = result

        if not result.passed:
            self._block_count += 1
            logger.warning(
                f"🛑 BLOCKED {token_symbol} [{chain_id}] — "
                f"{result.risk_level} | Flags: {result.flags}"
            )
        else:
            logger.info(
                f"✅ Security passed: {token_symbol} | "
                f"Risk: {result.risk_level} | "
                f"Taxes: {result.buy_tax:.1f}/{result.sell_tax:.1f}%"
            )

        return result

    async def _run_checks(self, token_address: str, chain_id: str) -> SecurityResult:
        """Run all checks and build the result."""
        result = SecurityResult(
            token_address=token_address,
            chain_id=chain_id,
            passed=False,
            risk_level="UNKNOWN"
        )

        try:
            goplus_data = await self._fetch_goplus(token_address, chain_id)
            if goplus_data:
                self._parse_goplus(goplus_data, result)
            else:
                # If GoPlus unavailable, run basic checks only
                result.warnings.append("GoPlus unavailable — basic checks only")
                await self._basic_dexscreener_check(token_address, chain_id, result)

        except Exception as e:
            logger.error(f"Security check error for {token_address}: {e}")
            result.flags.append("Check failed — skipping for safety")
            result.risk_level = "BLOCK"
            result.passed = False
            return result

        # Final pass/fail decision
        result.passed = self._make_decision(result)
        return result

    async def _fetch_goplus(self, token_address: str, chain_id: str) -> Optional[dict]:
        """Fetch token security data from GoPlus API."""
        goplus_chain = GOPLUS_CHAIN_IDS.get(chain_id, chain_id)

        if chain_id == "solana":
            url = f"{GOPLUS_API}/solana/token_security?contract_addresses={token_address}"
        else:
            url = f"{GOPLUS_API}/token_security/{goplus_chain}?contract_addresses={token_address}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    result_data = data.get("result", {})
                    # GoPlus returns address-keyed dict
                    key = token_address.lower()
                    return result_data.get(key) or result_data.get(token_address) or None
        except asyncio.TimeoutError:
            logger.warning(f"GoPlus timeout for {token_address[:10]}...")
            return None
        except Exception as e:
            logger.debug(f"GoPlus fetch error: {e}")
            return None

    def _parse_goplus(self, data: dict, result: SecurityResult):
        """Parse GoPlus response into our SecurityResult."""

        # Honeypot
        result.honeypot = data.get("is_honeypot", "0") == "1"
        if result.honeypot:
            result.flags.append("HONEYPOT — cannot sell")

        # Tax rates
        try:
            result.buy_tax = float(data.get("buy_tax", 0)) * 100
            result.sell_tax = float(data.get("sell_tax", 0)) * 100
        except (ValueError, TypeError):
            result.buy_tax = 0
            result.sell_tax = 0

        if result.buy_tax > self.max_buy_tax:
            result.flags.append(f"High buy tax {result.buy_tax:.1f}%")
        elif result.buy_tax > 5:
            result.warnings.append(f"Buy tax {result.buy_tax:.1f}%")

        if result.sell_tax > self.max_sell_tax:
            result.flags.append(f"High sell tax {result.sell_tax:.1f}%")
        elif result.sell_tax > 5:
            result.warnings.append(f"Sell tax {result.sell_tax:.1f}%")

        # Mint authority
        result.can_mint = (
            data.get("is_mintable", "0") == "1" or
            data.get("mint_authority") not in (None, "null", "", "0")
        )
        if result.can_mint:
            result.flags.append("Mintable — dev can print tokens")

        # Freeze authority (Solana specific)
        freeze = data.get("freeze_authority")
        result.can_freeze = freeze not in (None, "null", "", "0")
        if result.can_freeze:
            result.flags.append("Freeze authority enabled")

        # Blacklist
        result.has_blacklist = data.get("is_blacklisted", "0") == "1"
        if result.has_blacklist:
            result.flags.append("Has blacklist — dev can block sellers")

        # Proxy contract
        result.is_proxy = data.get("is_proxy", "0") == "1"
        if result.is_proxy:
            result.warnings.append("Proxy contract — hidden logic possible")

        # Holder concentration
        holders = data.get("holders", [])
        if holders:
            top10_pct = sum(
                float(h.get("percent", 0)) * 100
                for h in holders[:10]
            )
            result.top10_concentration = top10_pct
            if top10_pct > self.max_top10_concentration:
                result.flags.append(
                    f"Top10 hold {top10_pct:.1f}% — dump risk"
                )
            elif top10_pct > 60:
                result.warnings.append(
                    f"Top10 concentration {top10_pct:.1f}%"
                )

        # Dev wallet holdings
        creator = data.get("creator_address", "")
        if creator and holders:
            dev_holding = next(
                (float(h.get("percent", 0)) * 100
                 for h in holders
                 if h.get("address", "").lower() == creator.lower()),
                0
            )
            result.dev_holding_pct = dev_holding
            if dev_holding > self.max_dev_holding_pct:
                result.flags.append(
                    f"Dev holds {dev_holding:.1f}% — dump risk"
                )

        # Liquidity lock
        lp_locked = data.get("lp_locked_percent")
        result.liquidity_locked = (
            lp_locked is not None and
            float(lp_locked or 0) > 50
        )
        if not result.liquidity_locked:
            result.warnings.append("Liquidity not locked")

        # Risk level
        if result.flags:
            result.risk_level = "BLOCK" if any(
                k in " ".join(result.flags)
                for k in ["HONEYPOT", "Mintable", "blacklist", "dump risk"]
            ) else "DANGER"
        elif len(result.warnings) >= 3:
            result.risk_level = "CAUTION"
        else:
            result.risk_level = "SAFE"

    async def _basic_dexscreener_check(self, token_address: str,
                                        chain_id: str, result: SecurityResult):
        """Fallback checks using DexScreener when GoPlus is unavailable."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    data = await resp.json()
                    pairs = [
                        p for p in data.get("pairs", [])
                        if p.get("chainId") == chain_id
                    ]
                    if not pairs:
                        result.flags.append("No trading pairs found")
                        result.risk_level = "BLOCK"
                        return

                    pair = pairs[0]
                    liquidity = pair.get("liquidity", {}).get("usd", 0)
                    if liquidity < 5000:
                        result.flags.append(f"Very low liquidity ${liquidity:,.0f}")

                    result.risk_level = "CAUTION"
        except Exception as e:
            logger.debug(f"DexScreener fallback error: {e}")
            result.risk_level = "CAUTION"

    def _make_decision(self, result: SecurityResult) -> bool:
        """Final pass/fail logic."""
        # Hard blocks
        if self.block_honeypot and result.honeypot:
            return False
        if self.block_mintable and result.can_mint:
            return False
        if self.block_blacklist and result.has_blacklist:
            return False
        if result.buy_tax > self.max_buy_tax:
            return False
        if result.sell_tax > self.max_sell_tax:
            return False
        if result.top10_concentration > self.max_top10_concentration:
            return False
        if result.dev_holding_pct > self.max_dev_holding_pct:
            return False
        if result.risk_level == "BLOCK":
            return False

        return True

    def get_stats(self) -> dict:
        return {
            "total_checks": self._check_count,
            "blocked": self._block_count,
            "block_rate": (
                self._block_count / self._check_count * 100
                if self._check_count > 0 else 0
            ),
            "cache_size": len(self._cache)
        }

    def get_adjusted_slippage(self, result: SecurityResult,
                               base_slippage: float = 2.0) -> float:
        """
        Return recommended slippage based on token taxes.
        Adds a buffer on top of detected taxes to ensure trades execute.
        """
        tax_buffer = max(result.buy_tax, result.sell_tax)
        return max(base_slippage, tax_buffer + 2.0)
