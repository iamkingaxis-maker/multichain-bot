"""
Multi-Chain Scanner
Runs the $200k-$1m market cap scanner across Solana, Base, and BNB
simultaneously, sharing the same risk manager and performance tracker.
"""

import asyncio
import logging
import aiohttp
from typing import Optional
from chains.chain_config import ChainConfig
from utils.telegram_bot import TelegramNotifier

logger = logging.getLogger(__name__)

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"


class MultiChainScanner:
    def __init__(self, chain: ChainConfig, trader,
                 telegram: TelegramNotifier,
                 min_mcap: float = 200_000,
                 max_mcap: float = 1_000_000):
        self.chain = chain
        self.trader = trader
        self.telegram = telegram
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.seen_tokens: set = set()
        self.session: Optional[aiohttp.ClientSession] = None

    async def run(self):
        """Main scanner loop for this chain."""
        logger.info(
            f"[{self.chain.name}] 📡 Scanner started — "
            f"${self.min_mcap/1000:.0f}k-${self.max_mcap/1000:.0f}k"
        )
        async with aiohttp.ClientSession() as session:
            self.session = session
            while True:
                try:
                    await self._scan_cycle()
                except Exception as e:
                    logger.error(f"[{self.chain.name}] Scanner error: {e}")
                await asyncio.sleep(60)

    async def _scan_cycle(self):
        tokens = await self._fetch_tokens()
        if not tokens:
            return
        logger.info(f"[{self.chain.name}] 🔍 Evaluating {len(tokens)} tokens...")
        for token in tokens:
            try:
                await self._evaluate_token(token)
            except Exception as e:
                logger.debug(f"[{self.chain.name}] Token eval error: {e}")

    async def _fetch_tokens(self) -> list:
        """Fetch tokens in market cap range from DexScreener."""
        try:
            # Search for trending tokens on this chain
            url = f"{DEXSCREENER_API}/search?q={self.chain.dexscreener_chain}"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                pairs = data.get("pairs", [])

                return [
                    p for p in pairs
                    if p.get("chainId") == self.chain.dexscreener_chain
                    and self.min_mcap <= p.get("marketCap", 0) <= self.max_mcap
                ]
        except Exception as e:
            logger.error(f"[{self.chain.name}] Fetch error: {e}")
            return []

    async def _evaluate_token(self, pair: dict):
        """Score and evaluate a token."""
        token_address = pair.get("baseToken", {}).get("address", "")
        token_symbol = pair.get("baseToken", {}).get("symbol", "")
        token_name = pair.get("baseToken", {}).get("name", "Unknown")

        cache_key = f"{self.chain.chain_id}:{token_address}"
        if cache_key in self.seen_tokens:
            return
        self.seen_tokens.add(cache_key)

        # Pull metrics
        mcap = pair.get("marketCap", 0)
        volume_h1 = pair.get("volume", {}).get("h1", 0)
        price_change_h1 = pair.get("priceChange", {}).get("h1", 0)
        liquidity = pair.get("liquidity", {}).get("usd", 0)
        txns_h1 = pair.get("txns", {}).get("h1", {})
        buys_h1 = txns_h1.get("buys", 0)
        sells_h1 = txns_h1.get("sells", 0)

        score = 0
        red_flags = []
        reasons = []

        # Market cap position
        if mcap <= 400_000:
            score += 20
            reasons.append("+20 Early range")
        elif mcap <= 700_000:
            score += 15
            reasons.append("+15 Mid range")
        else:
            score += 10
            reasons.append("+10 Upper range")

        # Chain-adjusted volume thresholds
        # Base and BNB have different volume profiles than Solana
        vol_threshold_high = 30_000 if self.chain.chain_id != "solana" else 50_000
        vol_threshold_mid = 10_000 if self.chain.chain_id != "solana" else 20_000

        if volume_h1 >= vol_threshold_high:
            score += 20
            reasons.append(f"+20 Strong volume ${volume_h1:,.0f}")
        elif volume_h1 >= vol_threshold_mid:
            score += 12
            reasons.append(f"+12 Good volume ${volume_h1:,.0f}")
        elif volume_h1 >= 5_000:
            score += 6
            reasons.append(f"+6 Moderate volume ${volume_h1:,.0f}")
        else:
            red_flags.append(f"Low volume ${volume_h1:,.0f}")

        # Price momentum
        if price_change_h1 > 20:
            score += 20
            reasons.append(f"+20 Strong uptrend +{price_change_h1:.1f}%")
        elif price_change_h1 > 5:
            score += 12
            reasons.append(f"+12 Positive trend +{price_change_h1:.1f}%")
        elif price_change_h1 < -20:
            red_flags.append(f"Dumping {price_change_h1:.1f}%")

        # Buy pressure
        total = buys_h1 + sells_h1
        if total > 0:
            buy_ratio = buys_h1 / total
            if buy_ratio >= 0.65:
                score += 15
                reasons.append(f"+15 Buy pressure {buy_ratio*100:.0f}%")
            elif buy_ratio >= 0.50:
                score += 8
                reasons.append(f"+8 Balanced {buy_ratio*100:.0f}% buys")
            else:
                red_flags.append(f"Sell pressure {buy_ratio*100:.0f}% buys")

        # Liquidity (chain-adjusted minimums)
        min_liq = self.chain.min_liquidity_usd
        if liquidity >= min_liq * 3:
            score += 15
            reasons.append(f"+15 Good liquidity ${liquidity:,.0f}")
        elif liquidity >= min_liq:
            score += 8
            reasons.append(f"+8 Adequate liquidity ${liquidity:,.0f}")
        else:
            red_flags.append(f"Low liquidity ${liquidity:,.0f}")

        # Social links
        info = pair.get("info", {})
        if info.get("socials") or info.get("websites"):
            score += 10
            reasons.append("+10 Has social links")
        else:
            red_flags.append("No social links")

        passed = score >= 60 and len(red_flags) == 0
        logger.info(
            f"[{self.chain.name}] {'✅' if passed else '❌'} "
            f"{token_symbol} | Score: {score} | MCap: ${mcap:,.0f} | "
            f"Flags: {len(red_flags)}"
        )

        if passed and not self.trader.risk_manager.is_daily_limit_hit():
            await self._alert_and_buy(pair, score, reasons)

    async def _alert_and_buy(self, pair: dict, score: int, reasons: list):
        """Alert and execute buy."""
        token_address = pair.get("baseToken", {}).get("address", "")
        token_symbol = pair.get("baseToken", {}).get("symbol", "")
        token_name = pair.get("baseToken", {}).get("name", "Unknown")
        mcap = pair.get("marketCap", 0)
        volume_h1 = pair.get("volume", {}).get("h1", 0)
        price_change_h1 = pair.get("priceChange", {}).get("h1", 0)
        dex_url = pair.get("url", f"https://dexscreener.com/{self.chain.chain_id}/{token_address}")

        await self.telegram.send(
            f"🎯 *Scanner Signal: {token_name} (${token_symbol})*\n"
            f"🔗 Chain: {self.chain.name}\n\n"
            f"📊 Market Cap: ${mcap:,.0f}\n"
            f"📈 1h Change: {price_change_h1:+.1f}%\n"
            f"💧 1h Volume: ${volume_h1:,.0f}\n"
            f"⭐ Score: {score}/100\n\n"
            f"✅ *Reasons:*\n" + "\n".join(reasons[:5]) + "\n\n"
            f"[View on DexScreener]({dex_url})"
        )

        await self.trader.buy(
            token_address=token_address,
            token_symbol=token_symbol,
            reason=f"[{self.chain.name}] Scanner score {score}"
        )
