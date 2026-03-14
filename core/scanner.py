"""
Market Cap Scanner
Continuously scans for Solana tokens in the $200k-$1m market cap range
and scores them using multiple quality filters.
"""

import asyncio
import logging
import aiohttp
from datetime import datetime, timezone
from typing import Optional
from utils.telegram_bot import TelegramNotifier

logger = logging.getLogger(__name__)

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
BIRDEYE_API = "https://public-api.birdeye.so/defi"


class TokenScore:
    def __init__(self, token: dict):
        self.token = token
        self.score = 0
        self.reasons = []
        self.red_flags = []

    def add(self, points: int, reason: str):
        self.score += points
        self.reasons.append(f"+{points} {reason}")

    def flag(self, reason: str):
        self.red_flags.append(f"🚩 {reason}")

    @property
    def passed(self) -> bool:
        return self.score >= 60 and len(self.red_flags) == 0


class MarketCapScanner:
    def __init__(self, trader, telegram: TelegramNotifier,
                 min_mcap: float = 200_000, max_mcap: float = 1_000_000):
        self.trader = trader
        self.telegram = telegram
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.seen_tokens: set = set()
        self.session: Optional[aiohttp.ClientSession] = None

    async def run(self):
        """Main scanner loop."""
        logger.info(f"📡 Scanner started — watching ${self.min_mcap/1000:.0f}k-${self.max_mcap/1000:.0f}k range")
        async with aiohttp.ClientSession() as session:
            self.session = session
            while True:
                try:
                    await self._scan_cycle()
                except Exception as e:
                    logger.error(f"Scanner error: {e}")
                await asyncio.sleep(60)

    async def _scan_cycle(self):
        """One scan cycle — fetch and evaluate tokens."""
        tokens = await self._fetch_trending_tokens()
        if not tokens:
            return

        logger.info(f"🔍 Evaluating {len(tokens)} tokens...")
        for token in tokens:
            try:
                await self._evaluate_token(token)
            except Exception as e:
                logger.debug(f"Error evaluating token: {e}")

    async def _fetch_trending_tokens(self) -> list:
        """Fetch trending Solana tokens from DexScreener."""
        try:
            url = f"{DEXSCREENER_API}/tokens/solana"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                pairs = data.get("pairs", [])

                # Filter to our market cap range
                filtered = []
                for pair in pairs:
                    mcap = pair.get("marketCap", 0)
                    if self.min_mcap <= mcap <= self.max_mcap:
                        filtered.append(pair)

                return filtered
        except Exception as e:
            logger.error(f"DexScreener fetch error: {e}")
            return []

    async def _evaluate_token(self, pair: dict):
        """Score a token and decide whether to buy."""
        token_address = pair.get("baseToken", {}).get("address", "")
        token_name = pair.get("baseToken", {}).get("name", "Unknown")
        token_symbol = pair.get("baseToken", {}).get("symbol", "")

        # Skip already seen tokens
        if token_address in self.seen_tokens:
            return
        self.seen_tokens.add(token_address)

        score = TokenScore(pair)
        mcap = pair.get("marketCap", 0)
        volume_h1 = pair.get("volume", {}).get("h1", 0)
        volume_h6 = pair.get("volume", {}).get("h6", 0)
        price_change_h1 = pair.get("priceChange", {}).get("h1", 0)
        price_change_h6 = pair.get("priceChange", {}).get("h6", 0)
        liquidity = pair.get("liquidity", {}).get("usd", 0)
        txns_h1 = pair.get("txns", {}).get("h1", {})
        buys_h1 = txns_h1.get("buys", 0)
        sells_h1 = txns_h1.get("sells", 0)

        # --- SCORING ---

        # Market cap position (sweeter spot = higher score)
        if 200_000 <= mcap <= 400_000:
            score.add(20, "Early in range ($200k-$400k)")
        elif 400_000 <= mcap <= 700_000:
            score.add(15, "Mid range ($400k-$700k)")
        else:
            score.add(10, "Upper range ($700k-$1m)")

        # Volume momentum
        if volume_h1 >= 50_000:
            score.add(20, f"Strong 1h volume ${volume_h1:,.0f}")
        elif volume_h1 >= 20_000:
            score.add(12, f"Good 1h volume ${volume_h1:,.0f}")
        elif volume_h1 >= 10_000:
            score.add(6, f"Moderate 1h volume ${volume_h1:,.0f}")
        else:
            score.flag(f"Low 1h volume ${volume_h1:,.0f}")

        # Price momentum (going up = good)
        if price_change_h1 > 20:
            score.add(20, f"Strong uptrend +{price_change_h1:.1f}% 1h")
        elif price_change_h1 > 5:
            score.add(12, f"Positive trend +{price_change_h1:.1f}% 1h")
        elif price_change_h1 < -20:
            score.flag(f"Dumping -{abs(price_change_h1):.1f}% 1h")

        # Buy/sell ratio (more buys than sells = bullish)
        total_txns = buys_h1 + sells_h1
        if total_txns > 0:
            buy_ratio = buys_h1 / total_txns
            if buy_ratio >= 0.65:
                score.add(15, f"Buy pressure {buy_ratio*100:.0f}% buys")
            elif buy_ratio >= 0.50:
                score.add(8, f"Balanced trading {buy_ratio*100:.0f}% buys")
            else:
                score.flag(f"Sell pressure {buy_ratio*100:.0f}% buys")

        # Liquidity check
        if liquidity >= 50_000:
            score.add(15, f"Good liquidity ${liquidity:,.0f}")
        elif liquidity >= 20_000:
            score.add(8, f"Adequate liquidity ${liquidity:,.0f}")
        else:
            score.flag(f"Low liquidity ${liquidity:,.0f}")

        # Social links
        info = pair.get("info", {})
        socials = info.get("socials", [])
        websites = info.get("websites", [])
        if socials or websites:
            score.add(10, "Has social links")
        else:
            score.flag("No social links")

        # --- DECISION ---
        logger.info(
            f"{'✅' if score.passed else '❌'} {token_symbol} | "
            f"Score: {score.score} | MCap: ${mcap:,.0f} | "
            f"Flags: {len(score.red_flags)}"
        )

        if score.passed and not self.trader.risk_manager.is_daily_limit_hit():
            logger.info(f"🎯 BUY SIGNAL: {token_name} ({token_symbol})")
            await self._alert_and_buy(pair, score)

    async def _alert_and_buy(self, pair: dict, score: TokenScore):
        """Send alert and execute buy."""
        token_name = pair.get("baseToken", {}).get("name", "Unknown")
        token_symbol = pair.get("baseToken", {}).get("symbol", "")
        token_address = pair.get("baseToken", {}).get("address", "")
        mcap = pair.get("marketCap", 0)
        volume_h1 = pair.get("volume", {}).get("h1", 0)
        price_change_h1 = pair.get("priceChange", {}).get("h1", 0)
        dex_url = pair.get("url", f"https://dexscreener.com/solana/{token_address}")

        message = (
            f"🎯 *Scanner Signal: {token_name} (${token_symbol})*\n\n"
            f"📊 Market Cap: ${mcap:,.0f}\n"
            f"📈 1h Change: {price_change_h1:+.1f}%\n"
            f"💧 1h Volume: ${volume_h1:,.0f}\n"
            f"⭐ Score: {score.score}/100\n\n"
            f"✅ *Reasons:*\n" + "\n".join(score.reasons[:5]) + "\n\n"
            f"[View on DexScreener]({dex_url})"
        )
        await self.telegram.send(message)

        # Execute trade
        await self.trader.buy(
            token_address=token_address,
            token_symbol=token_symbol,
            reason=f"Scanner signal — score {score.score}"
        )
