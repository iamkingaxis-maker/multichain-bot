"""
Honeypot & Token Security Checker
Runs every token through multiple security checks before any buy.
Uses Rugcheck.xyz for Solana tokens; GoPlus Security API for EVM chains (Base, BSC).

Checks performed:
  - Honeypot detection (can you actually sell?)
  - Buy/sell tax / transfer fee detection
  - Mint authority (can dev print more tokens?)
  - Freeze authority (can dev freeze wallets?)
  - Blacklist function (can dev block sellers?)
  - Proxy contract (hidden logic?)
  - Top holder concentration
  - Dev wallet holdings
  - Liquidity lock status
  - Known rug detection (Rugcheck rugged flag)
  - Danger-level risk flags from Rugcheck
"""

import asyncio
import logging
import aiohttp
from dataclasses import dataclass, field
from typing import Optional, Dict
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

GOPLUS_API = "https://api.gopluslabs.io/api/v1"
RUGCHECK_API = "https://api.rugcheck.xyz/v1"

# GoPlus chain IDs (EVM only — Solana uses Rugcheck)
GOPLUS_CHAIN_IDS = {
    "base": "8453",
    "bsc":  "56"
}

# Known Solana AMM/DEX pool program addresses — these hold token supply
# as part of normal LP mechanics and should not count toward whale concentration.
_SOLANA_POOL_ADDRESSES_RAW = [
    # Raydium
    "5Q2hXp3CN4L8RG4A84RK5L6W3nNm9J5dHKGFwqXQEYq",  # Raydium AMM authority
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM program
    "HVh24gnqJTR46BUKiU8sSUDRFLCYGqxWqhCzl4WM4sv",  # Raydium CLMM authority
    # Orca Whirlpools
    "2LMKSFm7U4NRQgE3NJmEjw2MpJqHvbxnbejhZuRkC3B",  # Orca Whirlpool authority
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",  # Orca Whirlpool program
    # Meteora
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EkAXqzn",  # Meteora DLMM
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",  # Meteora LB pair program
    # pump.fun bonding curve & migration
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # pump.fun program
    "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1", # pump.fun bonding curve authority
    "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",  # pump.fun migration authority
    # Jupiter aggregator vaults
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",  # Jupiter v6 program
]
# Normalize to lowercase once at import time
SOLANA_POOL_ADDRESSES = {a.lower() for a in _SOLANA_POOL_ADDRESSES_RAW}


def _is_pool_address(address: str) -> bool:
    """Return True if the address is a known AMM pool/program (not a real holder)."""
    return address.lower() in SOLANA_POOL_ADDRESSES


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
    lp_lock_data_available: bool = False   # True only when GoPlus returned LP lock data
    _micro_cap: bool = False               # Passed through from check() call
    _bonding_curve: bool = False           # True = pump.fun pre-grad bonding curve (no LP exists)
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
        # Format: cache_key → (result, expires_at) where expires_at is time.time() + TTL
        self._cache: dict = {}
        self._check_count = 0
        self._block_count = 0

    async def check(self, token_address: str, chain_id: str,
                    token_symbol: str = "?",
                    micro_cap: bool = False,
                    bonding_curve: bool = False) -> SecurityResult:
        """
        Main entry point. Run all security checks on a token.
        Returns SecurityResult with passed=True only if safe to trade.

        micro_cap=True:    relaxes holder concentration threshold for fresh small-cap tokens.
        bonding_curve=True: skips LP lock requirement — pump.fun pre-graduation tokens have no
                            traditional LP to lock (the bonding curve IS the liquidity).
                            Use for pump-amm protocol only; NOT for pumpswap/raydium/meteora.
        """
        import time as _t
        _bc_tag = "bc" if bonding_curve else ("mc" if micro_cap else "std")
        cache_key = f"{token_address.lower()}:{chain_id}:{_bc_tag}"
        cached = self._cache.get(cache_key)
        if cached:
            result, expires_at = cached
            if _t.time() < expires_at:
                return result

        self._check_count += 1
        logger.info(f"🔍 Security check #{self._check_count}: {token_symbol} on {chain_id}")

        result = await self._run_checks(token_address, chain_id, micro_cap=micro_cap,
                                        bonding_curve=bonding_curve)

        self._cache[cache_key] = (result, _t.time() + self.cache_ttl)
        if len(self._cache) > 500:
            for old_key in list(self._cache.keys())[:100]:
                self._cache.pop(old_key, None)

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

    async def _run_checks(self, token_address: str, chain_id: str,
                          micro_cap: bool = False,
                          bonding_curve: bool = False) -> SecurityResult:
        """Run all checks and build the result."""
        result = SecurityResult(
            token_address=token_address,
            chain_id=chain_id,
            passed=False,
            risk_level="UNKNOWN",
            _micro_cap=micro_cap,
            _bonding_curve=bonding_curve,
        )

        try:
            if chain_id == "solana":
                # Use Rugcheck for Solana — GoPlus returns null for SPL tokens
                rugcheck_data = await self._fetch_rugcheck(token_address)
                if rugcheck_data:
                    self._parse_rugcheck(rugcheck_data, result, micro_cap=micro_cap,
                                        bonding_curve=bonding_curve)
                else:
                    result.warnings.append("Rugcheck unavailable — basic checks only")
                    await self._basic_dexscreener_check(token_address, chain_id, result)
            else:
                # EVM chains (Base, BSC) — use GoPlus
                goplus_data = await self._fetch_goplus(token_address, chain_id)
                if goplus_data:
                    self._parse_goplus(goplus_data, result)
                else:
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

    # Valid Solana base58 address: 32–44 chars, no 0/O/I/l
    _BASE58_RE = __import__("re").compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

    async def _fetch_rugcheck(self, mint: str) -> Optional[dict]:
        """Fetch Solana token security data from Rugcheck.xyz API (no key required)."""
        # Validate address format before hitting the API.
        # Fresh pump.fun tokens get a 400 from rugcheck because they aren't indexed
        # yet (they're seconds old) — that is NOT a bad address, so we must not
        # conflate the two.  Only return the hard-block sentinel when the address
        # itself is obviously malformed.
        if not self._BASE58_RE.match(mint):
            logger.warning(
                f"Rugcheck: bad address format for {mint[:10]} — blocking"
            )
            return {"_invalid_address": True}

        url = f"{RUGCHECK_API}/tokens/{mint}/report/summary"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 400:
                        # 400 from rugcheck on a valid address = token not yet indexed
                        # (common for fresh launches <5 min old).  Fall back to other
                        # checks (DexScreener, Axiom-provided sniper/dev fields).
                        logger.info(
                            f"Rugcheck 400 for {mint[:10]} — not indexed yet, skipping"
                        )
                        return None
                    if resp.status != 200:
                        logger.warning(
                            f"Rugcheck HTTP {resp.status} for {mint[:10]}..."
                        )
                        return None
                    data = await resp.json()
                    logger.debug(
                        f"Rugcheck raw for {mint[:10]}: "
                        f"score={data.get('score_normalised')} "
                        f"rugged={data.get('rugged')} "
                        f"lpLockedPct={data.get('lpLockedPct')} "
                        f"mint={data.get('mintAuthority')} "
                        f"freeze={data.get('freezeAuthority')} "
                        f"risks={[r.get('name') for r in data.get('risks', [])]}"
                    )
                    return data
        except asyncio.TimeoutError:
            logger.warning(f"Rugcheck timeout for {mint[:10]}...")
            return None
        except Exception as e:
            logger.debug(f"Rugcheck fetch error: {e}")
            return None

    def _parse_rugcheck(self, data: dict, result: SecurityResult, micro_cap: bool = False,
                        bonding_curve: bool = False):
        """Parse Rugcheck.xyz summary response into SecurityResult."""

        # Invalid address sentinel — address format rejected by Rugcheck (e.g. bad base58)
        if data.get("_invalid_address"):
            result.flags.append("BLOCK: invalid token address — rugcheck rejected (bad base58)")
            result.risk_level = "BLOCK"
            return

        # Known rug — hard block
        if data.get("rugged"):
            result.honeypot = True
            result.flags.append("HONEYPOT: known rug (Rugcheck rugged=True)")

        # Mint authority — None means revoked (safe)
        result.can_mint = data.get("mintAuthority") is not None
        if result.can_mint:
            result.flags.append("Mintable token — dev can mint")

        # Freeze authority — None means revoked (safe)
        result.can_freeze = data.get("freezeAuthority") is not None
        if result.can_freeze:
            result.flags.append("Freeze authority active")

        # Transfer fee (acts like sell tax on Solana)
        transfer_fee_pct = 0.0
        tf = data.get("transferFee")
        if isinstance(tf, dict):
            try:
                transfer_fee_pct = float(tf.get("pct", 0) or 0)
            except (ValueError, TypeError):
                transfer_fee_pct = 0.0
        result.sell_tax = transfer_fee_pct  # map to sell_tax field for downstream logic
        if transfer_fee_pct > self.max_sell_tax:
            result.flags.append(f"High transfer fee {transfer_fee_pct:.1f}% — acts as sell tax")
        elif transfer_fee_pct > 5:
            result.warnings.append(f"Transfer fee {transfer_fee_pct:.1f}%")

        # LP lock
        lp_locked_pct = data.get("lpLockedPct")
        result.lp_lock_data_available = lp_locked_pct is not None
        if lp_locked_pct is not None:
            result.liquidity_locked = float(lp_locked_pct or 0) > 0
            if not result.liquidity_locked:
                if bonding_curve:
                    # Pump.fun bonding curve — no traditional LP to lock, skip requirement
                    result.warnings.append("LP not locked (bonding curve — no LP exists pre-graduation)")
                else:
                    result.flags.append("Liquidity not locked — rug risk")
            else:
                logger.info(
                    f"Rugcheck: LP locked {lp_locked_pct:.1f}% "
                    f"for {result.token_address[:10]}..."
                )
        else:
            result.warnings.append("LP lock data unavailable")

        # Top holder concentration — sum top 10 real holders
        top_holders = data.get("topHolders", [])
        if top_holders:
            _LP_TAGS = {"lp", "liquidity", "liquiditypool", "pool", "amm", "bonding curve"}
            real_holders = [
                h for h in top_holders
                if h.get("insider", False) is not True
                and h.get("tag", "").lower().strip() not in _LP_TAGS
                and not _is_pool_address(h.get("address", "") or "")
            ]
            top10_pct = sum(
                float(h.get("pct", 0) or 0)
                for h in real_holders[:10]
            )
            result.top10_concentration = top10_pct
            # micro_cap: slightly relaxed (80%) vs standard threshold, but still hard blocks
            # 95% was too loose — 80%+ concentration is a rug setup regardless of age
            mc_threshold = 80.0 if micro_cap else self.max_top10_concentration
            if top10_pct > mc_threshold:
                result.flags.append(
                    f"Top10 hold {top10_pct:.1f}% — dump risk"
                )
            elif top10_pct > 60:
                result.warnings.append(f"Top10 concentration {top10_pct:.1f}%")

        # Danger-level risks from Rugcheck risks[]
        risks = data.get("risks", [])
        danger_risks = [r for r in risks if r.get("level") == "danger"]
        warn_risks = [r for r in risks if r.get("level") == "warn"]

        # In micro_cap mode, only LP-structure risks are expected for fresh launches
        # (owner hasn't had time to lock LP yet). Concentration and liquidity danger
        # flags are NOT structural — a single holder with 80%+ and low liquidity is
        # a rug setup regardless of token age. Keep those as hard blocks.
        _MC_WARN_KEYWORDS = (
            "lp unlocked", "liquidity not locked",
            "large amount of lp",
        )

        for risk in danger_risks:
            name = risk.get("name", "Unknown risk")
            desc = risk.get("description", "")
            flag_text = f"{name}: {desc}" if desc else name
            flag_lower = flag_text.lower()

            if bonding_curve and any(kw in flag_lower for kw in _MC_WARN_KEYWORDS):
                # Bonding curve — no LP exists to lock, so "LP unlocked" is structural
                result.warnings.append(flag_text)
            else:
                result.flags.append(flag_text)
            logger.info(
                f"Rugcheck danger risk for {result.token_address[:10]}: {flag_text}"
            )

        for risk in warn_risks:
            name = risk.get("name", "Unknown warning")
            desc = risk.get("description", "")
            warn_text = f"{name}: {desc}" if desc else name
            result.warnings.append(warn_text)

        # Overall risk score (informational)
        score = data.get("score_normalised")
        if score is not None:
            try:
                score_val = float(score)
                if score_val >= 80:
                    result.warnings.append(f"Rugcheck risk score {score_val:.0f}/100 (high)")
                elif score_val >= 50:
                    result.warnings.append(f"Rugcheck risk score {score_val:.0f}/100 (medium)")
            except (ValueError, TypeError):
                pass

        # Risk level determination: any danger flag from Rugcheck → BLOCK.
        # Danger flags include LP unlocked, creator rug history, mint/freeze authority,
        # high concentration. All are serious enough to skip the token.
        if result.flags:
            result.risk_level = "BLOCK"
        elif len(result.warnings) >= 3:
            result.risk_level = "CAUTION"
        else:
            result.risk_level = "SAFE"

        logger.info(
            f"Rugcheck parsed {result.token_address[:10]}: "
            f"risk={result.risk_level} "
            f"mint={result.can_mint} freeze={result.can_freeze} "
            f"lp_locked={result.liquidity_locked} "
            f"top10={result.top10_concentration:.1f}% "
            f"flags={result.flags}"
        )

    async def _fetch_goplus(self, token_address: str, chain_id: str) -> Optional[dict]:
        """Fetch token security data from GoPlus API (EVM chains only)."""
        goplus_chain = GOPLUS_CHAIN_IDS.get(chain_id, chain_id)
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

        # Holder concentration — skip holders tagged as LP/pool by GoPlus or
        # matching known Solana AMM program addresses.
        # GoPlus Solana uses "account" as the address key.
        holders = data.get("holders", [])
        if holders:
            _LP_TAGS = {"lp", "liquidity", "liquiditypool", "pool", "amm", "bonding curve"}
            real_holders = [
                h for h in holders
                if h.get("tag", "").lower().strip() not in _LP_TAGS
                and not _is_pool_address(h.get("account", "") or h.get("address", ""))
            ]
            top10_pct = sum(
                float(h.get("percent", 0)) * 100
                for h in real_holders[:10]
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
                 if (h.get("account", "") or h.get("address", "")).lower() == creator.lower()
                 and not _is_pool_address(h.get("account", "") or h.get("address", ""))),
                0
            )
            result.dev_holding_pct = dev_holding
            if dev_holding > self.max_dev_holding_pct:
                result.flags.append(
                    f"Dev holds {dev_holding:.1f}% — dump risk"
                )

        # Liquidity lock
        lp_locked = data.get("lp_locked_percent")
        result.lp_lock_data_available = lp_locked is not None
        result.liquidity_locked = (
            lp_locked is not None and
            float(lp_locked or 0) > 50
        )
        if result.lp_lock_data_available and not result.liquidity_locked:
            result.flags.append("Liquidity not locked — rug risk")
        elif not result.lp_lock_data_available:
            result.warnings.append("LP lock data unavailable")

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

        # Unlocked LP is a hard block for all non-bonding-curve pools.
        # Bonding curve (pump.fun pre-graduation) is exempt — no LP exists to lock.
        # Graduated pools (PumpSwap, Raydium, Meteora, Orca) must have LP locked.
        if result.lp_lock_data_available and not result.liquidity_locked and not result._bonding_curve:
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
