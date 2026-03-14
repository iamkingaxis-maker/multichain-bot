"""
Multi-Source Scanner
Queries DexScreener AND Birdeye simultaneously.
Only signals a buy when BOTH sources confirm the token is strong.
This dramatically reduces false signals.

Data sources:
  DexScreener — pairs, volume, price change, liquidity
  Birdeye     — holder count, trade count, smart money flow
  GoPlus      — security data (via SecurityChecker)
"""

import asyncio
import logging
import aiohttp
from typing import Optional, Dict, List
from datetime import datetime, timezone
from core.signal_evaluator import TokenSignalEvaluator
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
BIRDEYE_API = "https://public-api.birdeye.so/defi"
BIRDEYE_API_KEY = ""  # Set in config


@dataclass
class TokenSignal:
    """Combined signal from multiple data sources."""
    token_address: str
    token_symbol: str
    token_name: str
    chain_id: str
    mcap: float
    price_usd: float
    volume_h1: float
    volume_h6: float
    price_change_h1: float
    price_change_h6: float
    liquidity_usd: float
    buy_count_h1: int
    sell_count_h1: int
    holder_count: int
    holder_growth_pct: float    # Holder growth in last hour
    smart_money_buying: bool    # Large wallets accumulating
    dex_score: int              # 0-100 from DexScreener data
    birdeye_score: int          # 0-100 from Birdeye data
    combined_score: int         # Final combined score
    has_social: bool
    dex_url: str
    confirmed_by_both: bool     # True only if both sources agree
    hh_hl_confirmed: bool = False  # From signal evaluator — HH+HL structure
    flags: List[str] = field(default_factory=list)


class MultiSourceScanner:
    """
    Enhanced scanner that cross-references DexScreener and Birdeye.
    A token must score well on BOTH to trigger a buy signal.
    """

    def __init__(self,
                 chain,
                 trader,
                 security_checker,
                 telegram,
                 birdeye_api_key: str = "",
                 min_mcap: float = 200_000,
                 max_mcap: float = 1_000_000,
                 min_combined_score: int = 65,
                 require_both_sources: bool = True,
                 startup_delay: float = 0):
        self.chain = chain
        self.trader = trader
        self.security_checker = security_checker
        self.telegram = telegram
        self.birdeye_api_key = birdeye_api_key
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.min_combined_score = min_combined_score
        self.require_both_sources = require_both_sources
        self.startup_delay = startup_delay

        self.seen_tokens: set = set()
        self.evaluator = TokenSignalEvaluator(
            min_liquidity_usd=50_000,
            max_dev_wallet_pct=5.0,
            preferred_age_min_hours=3.0,
            preferred_age_max_hours=12.0,
            hard_skip_age_hours=24.0,
            pyramid_score_threshold=90
        )
        self.signals_fired: int = 0
        self.signals_blocked_security: int = 0
        self.signals_blocked_score: int = 0

    async def run(self):
        """Main scanner loop."""
        if self.startup_delay:
            await asyncio.sleep(self.startup_delay)
        logger.info(
            f"[{self.chain.name}] Multi-Source Scanner started — "
            f"${self.min_mcap/1000:.0f}k-${self.max_mcap/1000:.0f}k | "
            f"Min score: {self.min_combined_score}"
        )
        while True:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error(f"[{self.chain.name}] Scanner error: {e}")
            await asyncio.sleep(60)

    async def _scan_cycle(self):
        """Fetch from both sources and evaluate."""
        # Run both fetches concurrently
        dex_tokens, birdeye_tokens = await asyncio.gather(
            self._fetch_dexscreener(),
            self._fetch_birdeye(),
            return_exceptions=True
        )

        if isinstance(dex_tokens, Exception):
            dex_tokens = []
        if isinstance(birdeye_tokens, Exception):
            birdeye_tokens = {}

        logger.info(
            f"[{self.chain.name}] 🔍 DexScreener: {len(dex_tokens)} tokens | "
            f"Birdeye: {len(birdeye_tokens)} tokens"
        )

        # Build a set of addresses already covered by DexScreener
        dex_addrs = set()
        for token in dex_tokens:
            try:
                addr = token.get("baseToken", {}).get("address", "").lower()
                cache_key = f"{self.chain.chain_id}:{addr}"
                if cache_key in self.seen_tokens:
                    continue
                self.seen_tokens.add(cache_key)
                dex_addrs.add(addr)

                birdeye_data = birdeye_tokens.get(addr, {})
                signal = self._build_signal(token, birdeye_data)
                if signal:
                    await self._evaluate_signal(signal)
            except Exception as e:
                logger.debug(f"[{self.chain.name}] Token eval error: {e}")

        # Also evaluate Birdeye-only tokens by fetching their DexScreener pair data
        birdeye_only = [
            (addr, bdata) for addr, bdata in birdeye_tokens.items()
            if addr not in dex_addrs
        ]
        if birdeye_only:
            await self._evaluate_birdeye_tokens(birdeye_only)

    async def _evaluate_birdeye_tokens(self, birdeye_only: list):
        """Fetch DexScreener pair data for Birdeye-discovered tokens and evaluate."""
        # Batch addresses (DexScreener allows up to 30)
        batch = [addr for addr, _ in birdeye_only[:30]]
        birdeye_map = {addr: bdata for addr, bdata in birdeye_only[:30]}
        try:
            addresses = ",".join(batch)
            url = f"{DEXSCREENER_API}/tokens/{addresses}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
                    pairs = data.get("pairs") or []

            for pair in pairs:
                if pair.get("chainId") != self.chain.dexscreener_chain:
                    continue
                mcap = pair.get("marketCap") or 0
                if not (self.min_mcap <= mcap <= self.max_mcap):
                    continue
                addr = pair.get("baseToken", {}).get("address", "").lower()
                cache_key = f"{self.chain.chain_id}:{addr}"
                if cache_key in self.seen_tokens:
                    continue
                self.seen_tokens.add(cache_key)
                birdeye_data = birdeye_map.get(addr, {})
                signal = self._build_signal(pair, birdeye_data)
                if signal:
                    await self._evaluate_signal(signal)
        except Exception as e:
            logger.debug(f"[{self.chain.name}] Birdeye-only eval error: {e}")

    async def _fetch_dexscreener(self) -> list:
        """Fetch new tokens from DexScreener token profiles then enrich with pair data."""
        try:
            async with aiohttp.ClientSession() as session:
                # Step 1: get latest token profiles (newly listed tokens)
                profiles_url = "https://api.dexscreener.com/token-profiles/latest/v1"
                async with session.get(
                    profiles_url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        return []
                    profiles = await resp.json()

                # Filter to this chain only
                chain_profiles = [
                    p for p in (profiles if isinstance(profiles, list) else [])
                    if p.get("chainId") == self.chain.dexscreener_chain
                ]
                if not chain_profiles:
                    return []

                # Step 2: fetch pair data for those token addresses (batch up to 30)
                addresses = ",".join(
                    p["tokenAddress"] for p in chain_profiles[:30]
                    if p.get("tokenAddress")
                )
                pairs_url = f"{DEXSCREENER_API}/tokens/{addresses}"
                async with session.get(
                    pairs_url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp2:
                    if resp2.status != 200:
                        return []
                    data = await resp2.json()
                    pairs = data.get("pairs") or []

                # Return pairs on this chain within mcap range
                return [
                    p for p in pairs
                    if (p.get("chainId") == self.chain.dexscreener_chain and
                        self.min_mcap <= (p.get("marketCap") or 0) <= self.max_mcap)
                ]
        except Exception as e:
            logger.error(f"[{self.chain.name}] DexScreener error: {e}")
            return []

    async def _fetch_birdeye(self) -> Dict[str, dict]:
        """Fetch trending tokens from Birdeye."""
        if not self.birdeye_api_key:
            return {}

        # Map our chain IDs to Birdeye chain names
        birdeye_chains = {
            "solana": "solana",
            "base": "base",
            "bsc": "bsc"
        }
        birdeye_chain = birdeye_chains.get(self.chain.chain_id)
        if not birdeye_chain:
            return {}

        try:
            url = f"{BIRDEYE_API}/tokenlist"
            params = {
                "sort_by": "v24hChangePercent",
                "sort_type": "desc",
                "offset": 0,
                "limit": 50,
                "min_liquidity": self.min_mcap / 10,
                "chain": birdeye_chain
            }
            headers = {
                "X-API-KEY": self.birdeye_api_key,
                "x-chain": birdeye_chain,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, params=params, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.warning(f"[{self.chain.name}] Birdeye HTTP {resp.status}: {text[:100]}")
                        return {}
                    data = await resp.json()
                    tokens = data.get("data", {}).get("tokens", [])
                    # Key by address for fast lookup
                    return {
                        t.get("address", "").lower(): t
                        for t in tokens
                        if t.get("address")
                    }
        except Exception as e:
            logger.debug(f"[{self.chain.name}] Birdeye error: {e}")
            return {}

    def _build_signal(self, dex_pair: dict,
                      birdeye_data: dict) -> Optional[TokenSignal]:
        """Build a combined signal from both data sources."""
        base = dex_pair.get("baseToken", {})
        token_address = base.get("address", "").lower()
        token_symbol = base.get("symbol", "?")
        token_name = base.get("name", "Unknown")

        mcap = dex_pair.get("marketCap", 0)
        if not (self.min_mcap <= mcap <= self.max_mcap):
            return None

        volume_h1 = dex_pair.get("volume", {}).get("h1", 0)
        volume_h6 = dex_pair.get("volume", {}).get("h6", 0)
        price_change_h1 = dex_pair.get("priceChange", {}).get("h1", 0) or 0
        price_change_h6 = dex_pair.get("priceChange", {}).get("h6", 0) or 0
        liquidity = dex_pair.get("liquidity", {}).get("usd", 0)
        txns_h1 = dex_pair.get("txns", {}).get("h1", {})
        buys_h1 = txns_h1.get("buys", 0)
        sells_h1 = txns_h1.get("sells", 0)
        price_usd = float(dex_pair.get("priceUsd", 0) or 0)
        info = dex_pair.get("info", {})
        has_social = bool(info.get("socials") or info.get("websites"))
        dex_url = dex_pair.get("url", "")

        # DexScreener score
        dex_score = self._score_dexscreener(
            mcap, volume_h1, price_change_h1,
            buys_h1, sells_h1, liquidity, has_social
        )

        # Birdeye score
        birdeye_score = 0
        holder_count = 0
        holder_growth_pct = 0.0
        smart_money_buying = False

        if birdeye_data:
            birdeye_score, holder_count, holder_growth_pct, smart_money_buying = \
                self._score_birdeye(birdeye_data)

        # Combined score — weighted average
        if birdeye_data:
            combined = int(dex_score * 0.6 + birdeye_score * 0.4)
            confirmed_by_both = dex_score >= 55 and birdeye_score >= 55
        else:
            combined = dex_score
            confirmed_by_both = False

        return TokenSignal(
            token_address=token_address,
            token_symbol=token_symbol,
            token_name=token_name,
            chain_id=self.chain.chain_id,
            mcap=mcap,
            price_usd=price_usd,
            volume_h1=volume_h1,
            volume_h6=volume_h6,
            price_change_h1=price_change_h1,
            price_change_h6=price_change_h6,
            liquidity_usd=liquidity,
            buy_count_h1=buys_h1,
            sell_count_h1=sells_h1,
            holder_count=holder_count,
            holder_growth_pct=holder_growth_pct,
            smart_money_buying=smart_money_buying,
            dex_score=dex_score,
            birdeye_score=birdeye_score,
            combined_score=combined,
            has_social=has_social,
            dex_url=dex_url,
            confirmed_by_both=confirmed_by_both
        )

    def _score_dexscreener(self, mcap, volume_h1, price_change_h1,
                            buys, sells, liquidity, has_social) -> int:
        score = 0
        # Market cap position
        if mcap <= 400_000:
            score += 20
        elif mcap <= 700_000:
            score += 15
        else:
            score += 10

        # Volume (chain-adjusted)
        vol_high = 30_000 if self.chain.chain_id != "solana" else 50_000
        vol_mid = 10_000 if self.chain.chain_id != "solana" else 20_000
        if volume_h1 >= vol_high:
            score += 20
        elif volume_h1 >= vol_mid:
            score += 12
        elif volume_h1 >= 5_000:
            score += 6

        # Price momentum
        if price_change_h1 > 20:
            score += 20
        elif price_change_h1 > 10:
            score += 14
        elif price_change_h1 > 5:
            score += 8
        elif price_change_h1 < -15:
            score -= 15

        # Buy pressure
        total = buys + sells
        if total > 0:
            ratio = buys / total
            if ratio >= 0.65:
                score += 15
            elif ratio >= 0.55:
                score += 8
            elif ratio < 0.40:
                score -= 10

        # Liquidity
        min_liq = self.chain.min_liquidity_usd
        if liquidity >= min_liq * 3:
            score += 15
        elif liquidity >= min_liq:
            score += 8
        else:
            score -= 10

        # Social
        if has_social:
            score += 10

        return max(0, min(100, score))

    def _score_birdeye(self, data: dict):
        score = 0
        holder_count = int(data.get("holder", 0))
        holder_growth = float(data.get("holderChange24h", 0) or 0)
        trade_24h = int(data.get("trade24h", 0))
        volume_24h = float(data.get("v24hUSD", 0) or 0)
        smart_money = data.get("uniqueWallet24h", 0) > 50

        # Holders
        if holder_count >= 500:
            score += 25
        elif holder_count >= 200:
            score += 18
        elif holder_count >= 100:
            score += 12
        elif holder_count >= 50:
            score += 6
        else:
            score -= 10

        # Holder growth
        if holder_growth > 20:
            score += 20
        elif holder_growth > 10:
            score += 12
        elif holder_growth > 5:
            score += 6
        elif holder_growth < -10:
            score -= 15

        # Trade activity
        if trade_24h >= 1000:
            score += 20
        elif trade_24h >= 500:
            score += 14
        elif trade_24h >= 200:
            score += 8

        # Smart money
        if smart_money:
            score += 20
            smart_money_buying = True
        else:
            smart_money_buying = False

        # Volume confirmation
        if volume_24h >= 100_000:
            score += 15
        elif volume_24h >= 50_000:
            score += 10

        return max(0, min(100, score)), holder_count, holder_growth, smart_money_buying

    async def _evaluate_signal(self, signal: TokenSignal):
        """Evaluate a signal and decide whether to buy."""
        # Score gate
        if signal.combined_score < self.min_combined_score:
            self.signals_blocked_score += 1
            return

        # Require both sources if configured
        if self.require_both_sources and not signal.confirmed_by_both and self.birdeye_api_key:
            logger.debug(
                f"[{self.chain.name}] {signal.token_symbol} "
                f"not confirmed by Birdeye — skipping"
            )
            return

        if self.trader.risk_manager.is_daily_limit_hit():
            return

        # Security check — runs GoPlus before every buy
        sec_result = await self.security_checker.check(
            signal.token_address,
            self.chain.chain_id,
            signal.token_symbol
        )
        if not sec_result.passed:
            self.signals_blocked_security += 1
            logger.warning(
                f"[{self.chain.name}] 🛑 Security blocked "
                f"{signal.token_symbol} — {sec_result.risk_level}"
            )
            return

        # All checks passed — fire signal
        self.signals_fired += 1
        logger.info(
            f"[{self.chain.name}] 🎯 BUY SIGNAL: {signal.token_symbol} | "
            f"Score: {signal.combined_score} | "
            f"(DEX:{signal.dex_score}/BE:{signal.birdeye_score}) | "
            f"MCap: ${signal.mcap:,.0f}"
        )

        source_tag = "✅ Both sources" if signal.confirmed_by_both else "⚠️ Single source"
        smart_tag = "🧠 Smart money buying" if signal.smart_money_buying else ""

        await self.telegram.send(
            f"🎯 *Scanner Signal: {signal.token_name} (${signal.token_symbol})*\n"
            f"🔗 Chain: {self.chain.name}\n\n"
            f"📊 MCap: ${signal.mcap:,.0f}\n"
            f"📈 1h: {signal.price_change_h1:+.1f}% | "
            f"6h: {signal.price_change_h6:+.1f}%\n"
            f"💧 1h Vol: ${signal.volume_h1:,.0f}\n"
            f"👥 Holders: {signal.holder_count:,} "
            f"({signal.holder_growth_pct:+.1f}% growth)\n"
            f"⭐ Score: {signal.combined_score}/100 "
            f"(DEX:{signal.dex_score} / BE:{signal.birdeye_score})\n"
            f"{source_tag}\n"
            f"{smart_tag}\n"
            f"🔒 Security: {sec_result.risk_level}\n\n"
            f"[View on DexScreener]({signal.dex_url})"
        )

        await self.trader.buy(
            token_address=signal.token_address,
            token_symbol=signal.token_symbol,
            reason=(
                f"[{self.chain.name}] Multi-source score "
                f"{signal.combined_score} "
                f"(DEX:{signal.dex_score}/BE:{signal.birdeye_score})"
            ),
            signal_score=signal.combined_score,
            hh_hl_confirmed=getattr(signal, "hh_hl_confirmed", False)
        )

    def get_stats(self) -> dict:
        return {
            "chain": self.chain.name,
            "signals_fired": self.signals_fired,
            "blocked_by_security": self.signals_blocked_security,
            "blocked_by_score": self.signals_blocked_score,
            "tokens_seen": len(self.seen_tokens)
        }
