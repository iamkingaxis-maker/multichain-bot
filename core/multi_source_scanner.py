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
import time
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
    raw_pair_data: dict = field(default_factory=dict)  # Original DexScreener pair data


class MultiSourceScanner:
    """
    Enhanced scanner that cross-references DexScreener and Birdeye.
    A token must score well on BOTH to trigger a buy signal.
    """

    # Default keywords for DexScreener keyword search
    DEFAULT_KEYWORDS = [
        "solana", "sol meme", "new launch", "pump", "moon",
        "pepe", "doge", "cat", "ai", "trump"
    ]

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
                 startup_delay: float = 0,
                 sentiment_analyzer=None,
                 rug_classifier=None,
                 scanner_keywords: List[str] = None):
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
        self.sentiment_analyzer = sentiment_analyzer
        self.rug_classifier = rug_classifier
        self.scanner_keywords = scanner_keywords or self.DEFAULT_KEYWORDS

        self.seen_tokens: set = set()
        self._seen_tokens_order: list = []   # for LRU eviction
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

        # Watchlist — tokens scoring 45-64 (near misses)
        self.watchlist: Dict[str, dict] = {}
        self._watchlist_max = 20
        self._watchlist_ttl = 7200  # 2 hours in seconds

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
        """Fetch from all sources and evaluate."""
        # Run all fetches concurrently
        dex_tokens, keyword_tokens, birdeye_tokens = await asyncio.gather(
            self._fetch_dexscreener(),
            self._fetch_keyword_search(),
            self._fetch_birdeye(),
            return_exceptions=True
        )

        if isinstance(dex_tokens, Exception):
            dex_tokens = []
        if isinstance(keyword_tokens, Exception):
            keyword_tokens = []
        if isinstance(birdeye_tokens, Exception):
            birdeye_tokens = {}

        # Deduplicate keyword results with dex results
        dex_addr_set = {
            p.get("baseToken", {}).get("address", "").lower()
            for p in dex_tokens
        }
        for kp in keyword_tokens:
            addr = kp.get("baseToken", {}).get("address", "").lower()
            if addr not in dex_addr_set:
                dex_tokens.append(kp)
                dex_addr_set.add(addr)

        logger.info(
            f"[{self.chain.name}] DexScreener: {len(dex_tokens)} tokens "
            f"(incl keyword) | Birdeye: {len(birdeye_tokens)} tokens"
        )

        # Prune stale watchlist entries
        self._prune_watchlist()

        new_this_cycle = 0

        # Build a set of addresses already covered by DexScreener
        dex_addrs = set()
        for token in dex_tokens:
            try:
                addr = token.get("baseToken", {}).get("address", "").lower()
                cache_key = f"{self.chain.chain_id}:{addr}"
                if cache_key in self.seen_tokens:
                    continue
                self.seen_tokens.add(cache_key)
                self._seen_tokens_order.append(cache_key)
                # Evict oldest entries — keeps memory bounded and lets tokens re-score after 500 cycles
                if len(self._seen_tokens_order) > 500:
                    old = self._seen_tokens_order.pop(0)
                    self.seen_tokens.discard(old)
                dex_addrs.add(addr)
                new_this_cycle += 1

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

        # Send scan summary AFTER evaluation so counts are accurate
        await self.telegram.send(
            f"🔍 Scan complete | New tokens: {new_this_cycle} | "
            f"Seen total: {len(self.seen_tokens)} | "
            f"Signals fired: {self.signals_fired} | "
            f"Blocked score: {self.signals_blocked_score} | "
            f"Blocked security: {self.signals_blocked_security}"
        )

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
        """
        Fetch tokens from three DexScreener sources simultaneously:
          1. token-profiles/latest  — newly listed tokens
          2. token-boosts/latest    — tokens being actively boosted
          3. token-boosts/top       — top boosted tokens on the platform
        All three are deduplicated and enriched with pair data.
        """
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"User-Agent": "Mozilla/5.0"}

                # Fetch all three discovery endpoints concurrently
                endpoints = [
                    "https://api.dexscreener.com/token-profiles/latest/v1",
                    "https://api.dexscreener.com/token-boosts/latest/v1",
                    "https://api.dexscreener.com/token-boosts/top/v1",
                ]

                all_addresses: dict = {}  # address → source label (deduped)
                for url in endpoints:
                    try:
                        async with session.get(
                            url, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=15)
                        ) as resp:
                            if resp.status != 200:
                                continue
                            profiles = await resp.json()
                            source = url.split("/")[-2]  # "latest" or "top"
                            items = profiles if isinstance(profiles, list) else []
                            for p in items:
                                if p.get("chainId") != self.chain.dexscreener_chain:
                                    continue
                                addr = p.get("tokenAddress", "")
                                if addr and addr not in all_addresses:
                                    all_addresses[addr] = source
                    except Exception:
                        continue

                if not all_addresses:
                    return []

                # Fetch pair data in batches of 30
                all_pairs = []
                addr_list = list(all_addresses.keys())
                for i in range(0, len(addr_list), 30):
                    batch = ",".join(addr_list[i:i+30])
                    try:
                        async with session.get(
                            f"{DEXSCREENER_API}/tokens/{batch}",
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=15)
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                all_pairs.extend(data.get("pairs") or [])
                    except Exception:
                        continue

                # Filter to this chain + mcap range, keep highest-liquidity pair per token
                seen_addrs = set()
                result = []
                for p in sorted(
                    all_pairs,
                    key=lambda x: x.get("liquidity", {}).get("usd", 0),
                    reverse=True
                ):
                    if p.get("chainId") != self.chain.dexscreener_chain:
                        continue
                    mcap = p.get("marketCap") or 0
                    if not (self.min_mcap <= mcap <= self.max_mcap):
                        continue
                    addr = p.get("baseToken", {}).get("address", "").lower()
                    if addr in seen_addrs:
                        continue
                    seen_addrs.add(addr)
                    result.append(p)

                return result

        except Exception as e:
            logger.error(f"[{self.chain.name}] DexScreener error: {e}")
            return []

    async def _fetch_keyword_search(self) -> list:
        """
        4th discovery source: search DexScreener by keywords.
        Returns pair dicts in the same format as _fetch_dexscreener.
        """
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"User-Agent": "Mozilla/5.0"}
                all_pairs = []
                seen_addrs = set()

                for keyword in self.scanner_keywords:
                    try:
                        url = f"https://api.dexscreener.com/latest/dex/search?q={keyword}"
                        async with session.get(
                            url, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=15)
                        ) as resp:
                            if resp.status != 200:
                                continue
                            data = await resp.json()
                            pairs = data.get("pairs") or []
                            for p in pairs:
                                if p.get("chainId") != self.chain.dexscreener_chain:
                                    continue
                                mcap = p.get("marketCap") or 0
                                if not (self.min_mcap <= mcap <= self.max_mcap):
                                    continue
                                addr = p.get("baseToken", {}).get("address", "").lower()
                                if addr in seen_addrs:
                                    continue
                                seen_addrs.add(addr)
                                all_pairs.append(p)
                        # Small delay between keyword searches to be polite
                        await asyncio.sleep(0.5)
                    except Exception:
                        continue

                logger.info(
                    f"[{self.chain.name}] Keyword search: {len(all_pairs)} tokens "
                    f"from {len(self.scanner_keywords)} keywords"
                )
                return all_pairs
        except Exception as e:
            logger.error(f"[{self.chain.name}] Keyword search error: {e}")
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
            headers = {
                "X-API-KEY": self.birdeye_api_key,
                "x-chain": birdeye_chain,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            }
            # Fetch two pages with different sort criteria to maximise token coverage
            fetch_params = [
                {"sort_by": "v24hChangePercent", "sort_type": "desc", "offset": 0,  "limit": 50, "min_liquidity": self.min_mcap / 10},
                {"sort_by": "v24hUSD",           "sort_type": "desc", "offset": 0,  "limit": 50, "min_liquidity": self.min_mcap / 10},
            ]
            all_tokens: Dict[str, dict] = {}
            async with aiohttp.ClientSession() as session:
                for params in fetch_params:
                    async with session.get(
                        url, params=params, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15)
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            logger.warning(f"[{self.chain.name}] Birdeye HTTP {resp.status}: {text[:100]}")
                            continue
                        data = await resp.json()
                        tokens = data.get("data", {}).get("tokens", [])
                        all_tokens.update({
                            t.get("address", "").lower(): t
                            for t in tokens
                            if t.get("address")
                        })
                    await asyncio.sleep(1)  # small delay between pages
            return all_tokens
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

        # ── Dip Sniper: 24h drop >= 25% AND 1h recovering >= 5% ──
        flags = []
        price_change_h24 = dex_pair.get("priceChange", {}).get("h24", 0) or 0
        dip_setup = False
        if price_change_h24 <= -25 and price_change_h1 >= 5:
            dip_setup = True
            combined += 15
            combined = min(combined, 100)
            flags.append("dip_setup")
            logger.info(
                f"[{self.chain.name}] DIP DETECTED: {token_symbol} | "
                f"24h: {price_change_h24:+.1f}% | 1h: {price_change_h1:+.1f}% | "
                f"Score +15 -> {combined}"
            )

        # ── Pump Chaser: 1h change >= 20% AND buy ratio >= 0.65 AND vol >= $20k ──
        total_txns = buys_h1 + sells_h1
        buy_ratio = buys_h1 / total_txns if total_txns > 0 else 0
        pump_setup = False
        if price_change_h1 >= 20 and buy_ratio >= 0.65 and volume_h1 >= 20_000:
            pump_setup = True
            combined += 10
            combined = min(combined, 100)
            flags.append("pump_setup")
            logger.info(
                f"[{self.chain.name}] PUMP DETECTED: {token_symbol} | "
                f"1h: {price_change_h1:+.1f}% | Buy ratio: {buy_ratio:.2f} | "
                f"Vol: ${volume_h1:,.0f} | Score +10 -> {combined}"
            )

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
            confirmed_by_both=confirmed_by_both,
            flags=flags,
            raw_pair_data=dex_pair
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

            # Watchlist: tokens scoring 45-64 are near-misses worth tracking
            if 45 <= signal.combined_score < self.min_combined_score:
                setup_tags = []
                if "dip_setup" in signal.flags:
                    setup_tags.append("Dip recovery")
                if "pump_setup" in signal.flags:
                    setup_tags.append("Pump momentum")
                reason = ", ".join(setup_tags) if setup_tags else (
                    f"Score {signal.combined_score} "
                    f"(DEX:{signal.dex_score}/BE:{signal.birdeye_score})"
                )
                self._add_to_watchlist(signal, reason)

            logger.info(
                f"[{self.chain.name}] Low score: {signal.token_symbol} | "
                f"Score: {signal.combined_score} (need {self.min_combined_score}) | "
                f"DEX:{signal.dex_score} | "
                f"MCap: ${signal.mcap:,.0f} | "
                f"Vol1h: ${signal.volume_h1:,.0f} | "
                f"1h: {signal.price_change_h1:+.1f}%"
            )
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

        # Sentiment check — Twitter/Telegram/community presence
        if self.sentiment_analyzer:
            sent = await self.sentiment_analyzer.analyze(
                signal.token_address,
                signal.token_symbol,
                signal.chain_id,
                signal.raw_pair_data
            )
            if not sent.passed:
                self.signals_blocked_score += 1
                logger.info(
                    f"[{self.chain.name}] 🚫 Sentiment blocked "
                    f"{signal.token_symbol} — {sent.sentiment_grade} "
                    f"({sent.sentiment_score}/100) | {', '.join(sent.flags) if sent.flags else 'below min score'}"
                )
                return

        # Rug classifier — ML/heuristic rug probability check
        if self.rug_classifier:
            from ml.rug_classifier import TokenFeatures
            total_txns = signal.buy_count_h1 + signal.sell_count_h1
            buy_ratio = signal.buy_count_h1 / total_txns if total_txns > 0 else 0.5
            features = TokenFeatures(
                token_address=signal.token_address,
                chain_id=signal.chain_id,
                timestamp=str(datetime.now(timezone.utc)),
                buy_sell_ratio_5min=buy_ratio,
                buy_sell_ratio_30min=buy_ratio,
                volume_first_5min_usd=signal.volume_h1 / 12 if signal.volume_h1 else 0,
                price_change_5min=signal.price_change_h1 / 12 if signal.price_change_h1 else 0,
                price_change_30min=signal.price_change_h1 / 2 if signal.price_change_h1 else 0,
                lp_amount_usd=signal.liquidity_usd,
                has_twitter=signal.has_social,
                has_telegram=signal.has_social,
            )
            rug_pred = await self.rug_classifier.predict(features)
            if not rug_pred.passed:
                self.signals_blocked_security += 1
                logger.warning(
                    f"[{self.chain.name}] 🛑 Rug classifier blocked "
                    f"{signal.token_symbol} — {rug_pred.summary()}"
                )
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

    async def process_external_signal(self,
                                     token_address: str,
                                     token_symbol: str,
                                     reason: str,
                                     signal_score: int = 70,
                                     strategy_tag: str = "external",
                                     skip_security: bool = False,
                                     price_usd: float = 0.0,
                                     liquidity_usd: float = 0.0,
                                     volume_h1: float = 0.0,
                                     ) -> bool:
        """
        Entry point for edge strategies (CrossWalletConvergence,
        CapitulationReversal) to route signals through the scanner's
        security checks before buying.

        Returns True if the buy was executed, False if blocked.
        """
        # Score gate (respects market conditions via min_combined_score)
        if signal_score < self.min_combined_score:
            self.signals_blocked_score += 1
            logger.info(
                f"[{self.chain.name}] [{strategy_tag}] ❌ Low score: "
                f"{token_symbol} | Score: {signal_score} "
                f"(need {self.min_combined_score})"
            )
            return False

        # Already holding?
        if token_address in self.trader.open_positions:
            logger.info(
                f"[{self.chain.name}] [{strategy_tag}] "
                f"Already holding {token_symbol} — skipping"
            )
            return False

        # Daily loss limit
        if self.trader.risk_manager.is_daily_limit_hit():
            return False

        # Security check (unless caller explicitly skips, e.g. convergence
        # where wallets already vetted the token)
        if not skip_security:
            sec_result = await self.security_checker.check(
                token_address,
                self.chain.chain_id,
                token_symbol
            )
            if not sec_result.passed:
                self.signals_blocked_security += 1
                logger.warning(
                    f"[{self.chain.name}] [{strategy_tag}] 🛑 Security blocked "
                    f"{token_symbol} — {sec_result.risk_level}"
                )
                return False

        # All checks passed — execute buy
        self.signals_fired += 1
        logger.info(
            f"[{self.chain.name}] [{strategy_tag}] 🎯 BUY SIGNAL: "
            f"{token_symbol} | Score: {signal_score} | {reason}"
        )

        await self.telegram.send(
            f"🎯 *{strategy_tag} Signal: ${token_symbol}*\n"
            f"🔗 Chain: {self.chain.name}\n"
            f"⭐ Score: {signal_score}\n"
            f"📝 {reason}"
        )

        await self.trader.buy(
            token_address=token_address,
            token_symbol=token_symbol,
            reason=f"[{self.chain.name}] {strategy_tag}: {reason}",
            signal_score=signal_score
        )
        return True

    # ── Watchlist management ────────────────────────────────────────────────

    def _add_to_watchlist(self, signal: TokenSignal, reason: str):
        """Add a near-miss token to the watchlist (cap at 20, drop lowest)."""
        entry = {
            "symbol": signal.token_symbol,
            "score": signal.combined_score,
            "mcap": signal.mcap,
            "price": signal.price_usd,
            "reason": reason,
            "timestamp": time.time(),
            "flags": signal.flags,
            "dex_url": signal.dex_url,
        }
        self.watchlist[signal.token_address] = entry

        # Cap at max size — drop lowest score
        if len(self.watchlist) > self._watchlist_max:
            worst_addr = min(
                self.watchlist, key=lambda a: self.watchlist[a]["score"]
            )
            del self.watchlist[worst_addr]

    def _prune_watchlist(self):
        """Remove watchlist entries older than 2 hours."""
        cutoff = time.time() - self._watchlist_ttl
        stale = [
            addr for addr, entry in self.watchlist.items()
            if entry["timestamp"] < cutoff
        ]
        for addr in stale:
            del self.watchlist[addr]

    def get_watchlist(self) -> list:
        """Return watchlist as a sorted list (highest score first)."""
        self._prune_watchlist()
        result = []
        for addr, entry in self.watchlist.items():
            item = dict(entry)
            item["token_address"] = addr
            item["age_seconds"] = int(time.time() - entry["timestamp"])
            result.append(item)
        result.sort(key=lambda x: x["score"], reverse=True)
        return result

    def get_stats(self) -> dict:
        return {
            "chain": self.chain.name,
            "signals_fired": self.signals_fired,
            "blocked_by_security": self.signals_blocked_security,
            "blocked_by_score": self.signals_blocked_score,
            "tokens_seen": len(self.seen_tokens)
        }
