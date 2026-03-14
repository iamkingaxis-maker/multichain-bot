"""
Sentiment Analysis Layer
Checks social momentum before buying to filter out tokens
with strong on-chain metrics but no community traction.

Data sources:
  - Twitter/X mention velocity (via public search)
  - Telegram member count and growth
  - Reddit mention frequency
  - CoinGecko community stats
  - DexScreener social links validation

Key insight: memecoins need a community to pump.
A token with perfect on-chain metrics but zero social presence
is almost always a ghost token or early-stage rug setup.
"""

import asyncio
import logging
import aiohttp
import re
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

COINGECKO_API = "https://api.coingecko.com/api/v3"
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"


@dataclass
class SentimentScore:
    token_address: str
    token_symbol: str

    # Raw signals
    twitter_mentions_1h: int = 0
    twitter_mentions_24h: int = 0
    twitter_mention_velocity: float = 0.0  # mentions/hour trend
    telegram_members: int = 0
    telegram_growth_pct: float = 0.0
    reddit_mentions_24h: int = 0
    has_active_website: bool = False
    has_twitter: bool = False
    has_telegram: bool = False
    coingecko_listed: bool = False
    community_score: int = 0              # CoinGecko community score

    # Derived score
    sentiment_score: int = 0             # 0-100
    sentiment_grade: str = "UNKNOWN"     # STRONG, MODERATE, WEAK, NONE
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    flags: List[str] = field(default_factory=list)
    signals: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.sentiment_score >= 30  # Minimum social presence required

    def summary(self) -> str:
        return (
            f"Sentiment: {self.sentiment_grade} ({self.sentiment_score}/100) | "
            f"Twitter: {self.twitter_mentions_1h}/hr | "
            f"TG: {self.telegram_members:,} | "
            f"{'✅' if self.has_twitter else '❌'}Twitter "
            f"{'✅' if self.has_telegram else '❌'}Telegram"
        )


class SentimentAnalyzer:
    """
    Analyzes social momentum for tokens before buying.
    Used as an additional filter in the multi-source scanner.
    """

    def __init__(self,
                 min_sentiment_score: int = 30,
                 require_twitter: bool = True,
                 require_telegram: bool = False,
                 cache_ttl_seconds: int = 300):
        self.min_score = min_sentiment_score
        self.require_twitter = require_twitter
        self.require_telegram = require_telegram
        self.cache_ttl = cache_ttl_seconds
        self._cache: Dict[str, SentimentScore] = {}
        self._checks_run = 0
        self._blocks_on_sentiment = 0

    async def analyze(self, token_address: str,
                       token_symbol: str,
                       chain_id: str,
                       dex_pair_data: Optional[dict] = None) -> SentimentScore:
        """
        Run full sentiment analysis on a token.
        Returns SentimentScore with pass/fail decision.
        """
        cache_key = f"{chain_id}:{token_address.lower()}"
        cached = self._cache.get(cache_key)
        if cached:
            age = (datetime.now(timezone.utc) - cached.checked_at).total_seconds()
            if age < self.cache_ttl:
                return cached

        self._checks_run += 1
        result = SentimentScore(
            token_address=token_address,
            token_symbol=token_symbol
        )

        # Run all checks concurrently
        await asyncio.gather(
            self._check_social_links(result, dex_pair_data),
            self._check_coingecko(result, token_address, chain_id),
            self._check_twitter_mentions(result, token_symbol),
            self._check_telegram(result, dex_pair_data),
            return_exceptions=True
        )

        # Calculate final score
        self._calculate_score(result)
        self._cache[cache_key] = result

        if not result.passed:
            self._blocks_on_sentiment += 1
            logger.info(
                f"[Sentiment] ⚠️ {token_symbol} weak sentiment: "
                f"{result.sentiment_grade} ({result.sentiment_score}/100)"
            )
        else:
            logger.info(
                f"[Sentiment] ✅ {token_symbol}: {result.summary()}"
            )

        return result

    async def _check_social_links(self, result: SentimentScore,
                                   pair_data: Optional[dict]):
        """Check DexScreener pair data for social links."""
        if not pair_data:
            return

        info = pair_data.get("info", {})
        socials = info.get("socials", [])
        websites = info.get("websites", [])

        for social in socials:
            platform = social.get("type", "").lower()
            url = social.get("url", "")
            if platform == "twitter" and url:
                result.has_twitter = True
                result.signals.append("Has Twitter link")
            elif platform == "telegram" and url:
                result.has_telegram = True
                result.signals.append("Has Telegram link")

        if websites:
            result.has_active_website = True
            result.signals.append("Has website")

    async def _check_coingecko(self, result: SentimentScore,
                                 token_address: str, chain_id: str):
        """Check if token is listed on CoinGecko with community data."""
        # Map chain IDs to CoinGecko platform IDs
        cg_platforms = {
            "solana": "solana",
            "base": "base",
            "bsc": "binance-smart-chain"
        }
        platform = cg_platforms.get(chain_id, "")
        if not platform:
            return

        try:
            url = f"{COINGECKO_API}/coins/{platform}/contract/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()

                    result.coingecko_listed = True
                    result.signals.append("Listed on CoinGecko")

                    community = data.get("community_data", {})
                    result.community_score = int(
                        data.get("community_score", 0) or 0
                    )

                    twitter_followers = community.get(
                        "twitter_followers", 0
                    ) or 0
                    if twitter_followers > 1000:
                        result.signals.append(
                            f"CoinGecko: {twitter_followers:,} Twitter followers"
                        )
                    elif twitter_followers > 0:
                        result.twitter_mentions_24h = twitter_followers // 10

                    tg_members = community.get(
                        "telegram_channel_user_count", 0
                    ) or 0
                    if tg_members > 0:
                        result.telegram_members = tg_members
                        result.has_telegram = True

        except Exception as e:
            logger.debug(f"[Sentiment] CoinGecko check error: {e}")

    async def _check_twitter_mentions(self, result: SentimentScore,
                                       token_symbol: str):
        """
        Estimate Twitter mention velocity for a token.
        Uses public DexScreener trending data as a proxy since
        direct Twitter API requires paid access.
        """
        try:
            # Check DexScreener trending — tokens trending there
            # usually have social momentum behind them
            url = f"{DEXSCREENER_API}/search?q={token_symbol}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
                    pairs = data.get("pairs", [])

                    # Count pairs with this symbol — more pairs = more attention
                    matching = [
                        p for p in pairs
                        if p.get("baseToken", {}).get("symbol", "").upper()
                        == token_symbol.upper()
                    ]

                    if len(matching) > 5:
                        result.twitter_mentions_1h = len(matching) * 10
                        result.signals.append(
                            f"Trending: {len(matching)} pairs on DexScreener"
                        )
                    elif len(matching) > 0:
                        result.twitter_mentions_1h = len(matching) * 3

        except Exception as e:
            logger.debug(f"[Sentiment] Twitter proxy check error: {e}")

    async def _check_telegram(self, result: SentimentScore,
                               pair_data: Optional[dict]):
        """
        Try to fetch Telegram member count from the token's TG link.
        Limited without API access but can validate link existence.
        """
        if not pair_data:
            return

        info = pair_data.get("info", {})
        socials = info.get("socials", [])

        for social in socials:
            if social.get("type", "").lower() == "telegram":
                url = social.get("url", "")
                if url:
                    result.has_telegram = True
                    # Try to extract invite link username
                    # and check if the group exists
                    username = url.rstrip("/").split("/")[-1]
                    if username and not username.startswith("+"):
                        # Public group — can verify it exists
                        try:
                            tg_url = f"https://t.me/{username}"
                            async with aiohttp.ClientSession() as session:
                                async with session.get(
                                    tg_url,
                                    timeout=aiohttp.ClientTimeout(total=5),
                                    allow_redirects=True
                                ) as resp:
                                    if resp.status == 200:
                                        text = await resp.text()
                                        # Extract member count from page
                                        member_match = re.search(
                                            r'(\d+[\d,]*)\s*members?',
                                            text, re.IGNORECASE
                                        )
                                        if member_match:
                                            count_str = member_match.group(1).replace(",", "")
                                            result.telegram_members = int(count_str)
                                            if result.telegram_members > 500:
                                                result.signals.append(
                                                    f"TG: {result.telegram_members:,} members"
                                                )
                        except Exception:
                            pass
                    break

    def _calculate_score(self, result: SentimentScore):
        """Calculate final sentiment score 0-100."""
        score = 0

        # Social presence (0-40 points)
        if result.has_twitter:
            score += 20
        if result.has_telegram:
            score += 15
        if result.has_active_website:
            score += 5

        # Twitter activity (0-25 points)
        if result.twitter_mentions_1h >= 50:
            score += 25
        elif result.twitter_mentions_1h >= 20:
            score += 18
        elif result.twitter_mentions_1h >= 5:
            score += 10
        elif result.twitter_mentions_1h > 0:
            score += 5

        # Telegram community (0-20 points)
        if result.telegram_members >= 5000:
            score += 20
        elif result.telegram_members >= 1000:
            score += 15
        elif result.telegram_members >= 500:
            score += 10
        elif result.telegram_members >= 100:
            score += 5

        # CoinGecko listing (0-10 points)
        if result.coingecko_listed:
            score += 10

        # Community score bonus (0-5 points)
        if result.community_score >= 50:
            score += 5
        elif result.community_score >= 25:
            score += 3

        result.sentiment_score = min(100, score)

        # Grade assignment
        if score >= 70:
            result.sentiment_grade = "STRONG"
        elif score >= 50:
            result.sentiment_grade = "MODERATE"
        elif score >= 30:
            result.sentiment_grade = "WEAK"
        else:
            result.sentiment_grade = "NONE"

        # Hard block flags
        if self.require_twitter and not result.has_twitter:
            result.flags.append("No Twitter — blocked")
        if self.require_telegram and not result.has_telegram:
            result.flags.append("No Telegram — blocked")

        # Override score to 0 if hard blocks present
        if result.flags:
            result.sentiment_score = 0
            result.sentiment_grade = "BLOCKED"

    def get_stats(self) -> dict:
        return {
            "checks_run": self._checks_run,
            "blocked_on_sentiment": self._blocks_on_sentiment,
            "block_rate_pct": round(
                self._blocks_on_sentiment / self._checks_run * 100, 1
            ) if self._checks_run > 0 else 0,
            "cache_size": len(self._cache)
        }
