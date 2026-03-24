"""
Token Signal Evaluator
Encodes the trader's exact scanner rules for evaluating tokens.

REQUIRED FOR HIGH CONFIDENCE:
  1. Volume accelerating — each candle bigger than last, no sudden drops
  2. Higher High AND Higher Low together (both required)
  3. Holder count growing fast
  4. Active Telegram community (not just a link — actual activity)
  5. Liquidity above $50k minimum

TOKEN AGE:
  Preferred: 3-12 hours old
  Too fresh (< 3h): survived initial dump not confirmed
  Too old (> 24h with no new highs): dead momentum — hard skip

HARD SKIP CONDITIONS (any one kills the trade):
  - High volume but very low holder count (bot manipulation)
  - Dev wallet > 5% of supply
  - Token > 24 hours old with no new highs in last 4 hours
  - h1 price change > 75% (overbought — pump likely over)
  - m5 price change < 0% (price falling right now — no entry)

PYRAMID UP:
  Only if original signal score was 90+
  Add 30% of original position after TP1 fires
  One pyramid per position maximum

SOCIAL:
  Telegram group activity is the real catalyst signal
  Twitter required as baseline but not the trigger
"""

import logging
import aiohttp
import asyncio
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

DEXSCREENER_TOKEN = "https://api.dexscreener.com/latest/dex/tokens/"


@dataclass
class CandleData:
    """Volume and price data for one time period."""
    period: str          # "m5", "h1", "h6", "h24"
    volume_usd: float
    price_change_pct: float
    buy_count: int
    sell_count: int
    price_high: float
    price_low: float


@dataclass
class TokenEvaluation:
    """Full evaluation result for one token."""
    token_address: str
    token_symbol: str
    token_name: str
    chain_id: str

    # Core metrics
    mcap: float
    liquidity_usd: float
    age_hours: float
    holder_count: int
    dev_wallet_pct: float

    # Volume analysis
    volume_accelerating: bool = False
    volume_acceleration_score: int = 0    # 0-30 points
    has_volume_drop: bool = False

    # Price structure
    higher_high: bool = False
    higher_low: bool = False
    hh_hl_confirmed: bool = False         # Both required
    price_structure_score: int = 0        # 0-25 points

    # Holder growth (Birdeye disabled — kept for compat but not scored)
    holder_growth_fast: bool = False
    holder_growth_score: int = 0          # unused — Birdeye disabled

    # Buy pressure (m5 buy/sell ratio)
    buy_pressure_m5_score: int = 0        # 0-20 points (replaces holder_growth)

    # Social
    has_telegram: bool = False
    telegram_active: bool = False         # Not just a link
    has_twitter: bool = False
    social_score: int = 0                 # 0-15 points

    # Liquidity
    liquidity_ok: bool = False
    liquidity_score: int = 0              # 0-10 points

    # Hard skip flags
    hard_skip: bool = False
    skip_reasons: List[str] = field(default_factory=list)

    # Bonus signals
    age_bonus: int = 0                    # +5 for 3-12h sweet spot
    warnings: List[str] = field(default_factory=list)

    # Final
    total_score: int = 0
    confidence: str = "LOW"              # LOW, MEDIUM, HIGH, VERY_HIGH
    pyramid_eligible: bool = False        # True if score >= 90

    def calculate_total(self):
        """Sum all component scores."""
        self.total_score = (
            self.volume_acceleration_score +
            self.price_structure_score +
            self.buy_pressure_m5_score +
            self.social_score +
            self.liquidity_score +
            self.age_bonus
        )
        if self.total_score >= 90:
            self.confidence = "VERY_HIGH"
            self.pyramid_eligible = True
        elif self.total_score >= 75:
            self.confidence = "HIGH"
        elif self.total_score >= 60:
            self.confidence = "MEDIUM"
        else:
            self.confidence = "LOW"

    def summary(self) -> str:
        parts = [f"Score: {self.total_score}/100 ({self.confidence})"]
        if self.hard_skip:
            parts.append(f"SKIP: {' | '.join(self.skip_reasons)}")
        if self.hh_hl_confirmed:
            parts.append("HH+HL ✅")
        if self.volume_accelerating:
            parts.append("Vol↑ ✅")
        if self.buy_pressure_m5_score >= 12:
            parts.append(f"BuyPressure↑ ✅")
        elif self.buy_pressure_m5_score == 0:
            parts.append("SellPressure ⚠️")
        if self.telegram_active:
            parts.append("TG Active ✅")
        return " | ".join(parts)


class TokenSignalEvaluator:
    """
    Evaluates tokens using the trader's exact scanner criteria.
    Used by MultiSourceScanner before making any buy decision.
    """

    def __init__(self,
                 min_liquidity_usd: float = 50_000,
                 max_dev_wallet_pct: float = 5.0,
                 preferred_age_min_hours: float = 3.0,
                 preferred_age_max_hours: float = 12.0,
                 hard_skip_age_hours: float = 24.0,
                 pyramid_score_threshold: int = 90,
                 min_holder_count: int = 100,
                 volume_acceleration_candles: int = 3):

        self.min_liquidity = min_liquidity_usd
        self.max_dev_pct = max_dev_wallet_pct
        self.preferred_age_min = preferred_age_min_hours
        self.preferred_age_max = preferred_age_max_hours
        self.hard_skip_age = hard_skip_age_hours
        self.pyramid_threshold = pyramid_score_threshold
        self.min_holders = min_holder_count
        self.vol_candles = volume_acceleration_candles

        self._cache: Dict[str, TokenEvaluation] = {}
        self._evaluations_run = 0
        self._hard_skips = 0

    async def evaluate(self, pair_data: dict,
                       birdeye_data: Optional[dict] = None,
                       security_result=None) -> TokenEvaluation:
        """
        Full evaluation of a token using trader's rules.
        Returns TokenEvaluation with pass/fail and score.
        """
        self._evaluations_run += 1

        base = pair_data.get("baseToken", {})
        token_address = base.get("address", "")
        token_symbol = base.get("symbol", "?")
        token_name = base.get("name", "Unknown")
        chain_id = pair_data.get("chainId", "")

        mcap = pair_data.get("marketCap", 0)
        liquidity = pair_data.get("liquidity", {}).get("usd", 0)
        price_usd = float(pair_data.get("priceUsd", 0) or 0)

        # Extract candle data
        volume = pair_data.get("volume", {})
        price_change = pair_data.get("priceChange", {})
        txns = pair_data.get("txns", {})

        candles = self._build_candles(volume, price_change, txns, price_usd)

        # Estimate token age from pair creation time
        age_hours = self._estimate_age(pair_data)

        # Holder data from Birdeye or default
        holder_count = int(birdeye_data.get("holder", 0)) \
            if birdeye_data else 0
        holder_change = float(birdeye_data.get("holderChange24h", 0) or 0) \
            if birdeye_data else 0

        # Dev wallet from security result
        dev_pct = security_result.dev_holding_pct \
            if security_result else 0.0

        # Social from pair info
        info = pair_data.get("info", {})
        socials = info.get("socials", [])
        has_twitter = any(
            s.get("type", "").lower() == "twitter" for s in socials
        )
        has_telegram = any(
            s.get("type", "").lower() == "telegram" for s in socials
        )
        tg_url = next(
            (s.get("url", "") for s in socials
             if s.get("type", "").lower() == "telegram"), ""
        )

        eval_result = TokenEvaluation(
            token_address=token_address,
            token_symbol=token_symbol,
            token_name=token_name,
            chain_id=chain_id,
            mcap=mcap,
            liquidity_usd=liquidity,
            age_hours=age_hours,
            holder_count=holder_count,
            dev_wallet_pct=dev_pct,
            has_twitter=has_twitter,
            has_telegram=has_telegram
        )

        # ── HARD SKIP CHECKS ─────────────────────────────────────────────

        # 1. Dev wallet > 5%
        if dev_pct > self.max_dev_pct:
            eval_result.hard_skip = True
            eval_result.skip_reasons.append(
                f"Dev holds {dev_pct:.1f}% (>{self.max_dev_pct}%)"
            )

        # 2. High volume but very low holder count (bot manipulation)
        vol_h1 = volume.get("h1", 0)
        if vol_h1 > 50_000 and holder_count > 0 and holder_count < 50:
            eval_result.hard_skip = True
            eval_result.skip_reasons.append(
                f"High volume ${vol_h1:,.0f} but only {holder_count} holders"
            )

        # 3. Token > 24h old with no new highs recently
        if age_hours > self.hard_skip_age:
            pc_h6 = price_change.get("h6", 0) or 0
            pc_h4 = price_change.get("h4", 0) or 0
            if pc_h6 <= 0 and pc_h4 <= 0:
                eval_result.hard_skip = True
                eval_result.skip_reasons.append(
                    f"Token {age_hours:.0f}h old, no new highs in 4-6h"
                )

        # 4. Overbought — h1 > 75% means the pump is likely over
        pc_h1 = price_change.get("h1", 0) or 0
        if pc_h1 > 75:
            eval_result.hard_skip = True
            eval_result.skip_reasons.append(
                f"Overbought: h1={pc_h1:+.0f}% — pump likely over"
            )

        # 5. Price falling right now — m5 < 0 means no current momentum
        pc_m5 = price_change.get("m5", 0) or 0
        if pc_m5 < 0:
            eval_result.hard_skip = True
            eval_result.skip_reasons.append(
                f"Price falling: m5={pc_m5:+.1f}% — wait for floor"
            )

        if eval_result.hard_skip:
            self._hard_skips += 1
            eval_result.calculate_total()
            return eval_result

        # ── VOLUME ACCELERATION (0-30 points) ────────────────────────────
        eval_result.volume_accelerating, eval_result.has_volume_drop = \
            self._check_volume_acceleration(candles)

        if eval_result.volume_accelerating and not eval_result.has_volume_drop:
            eval_result.volume_acceleration_score = 30
        elif eval_result.volume_accelerating:
            eval_result.volume_acceleration_score = 15
            eval_result.warnings.append("Volume accelerating but has drops")
        else:
            eval_result.volume_acceleration_score = 0

        # ── HIGHER HIGH + HIGHER LOW (0-25 points) ───────────────────────
        eval_result.higher_high, eval_result.higher_low = \
            self._check_price_structure(candles, price_change)

        eval_result.hh_hl_confirmed = (
            eval_result.higher_high and eval_result.higher_low
        )

        if eval_result.hh_hl_confirmed:
            eval_result.price_structure_score = 25
        elif eval_result.higher_high:
            eval_result.price_structure_score = 10
            eval_result.warnings.append("Higher high but no higher low")
        else:
            eval_result.price_structure_score = 0

        # ── BUY PRESSURE M5 (0-20 points) — replaces dead holder_growth ─────
        m5_txns = txns.get("m5", {})
        m5_buys = m5_txns.get("buys", 0)
        m5_sells = m5_txns.get("sells", 0)
        m5_total = m5_buys + m5_sells
        if m5_total > 0:
            m5_ratio = m5_buys / m5_total
            if m5_ratio >= 0.65:
                eval_result.buy_pressure_m5_score = 20
            elif m5_ratio >= 0.55:
                eval_result.buy_pressure_m5_score = 12
            elif m5_ratio >= 0.45:
                eval_result.buy_pressure_m5_score = 6
            else:
                eval_result.buy_pressure_m5_score = 0
                eval_result.warnings.append(
                    f"Sell pressure: m5 buy ratio {m5_ratio:.0%}"
                )
        else:
            eval_result.buy_pressure_m5_score = 7  # no data — neutral

        # ── HOLDER GROWTH (0-20 points) ───────────────────────────────────
        if holder_count >= 500 and holder_change > 20:
            eval_result.holder_growth_fast = True
            eval_result.holder_growth_score = 20
        elif holder_count >= 200 and holder_change > 10:
            eval_result.holder_growth_fast = True
            eval_result.holder_growth_score = 15
        elif holder_count >= 100 and holder_change > 5:
            eval_result.holder_growth_score = 10
        elif holder_count < self.min_holders:
            eval_result.holder_growth_score = 0
            eval_result.warnings.append(
                f"Low holder count: {holder_count}"
            )
        else:
            eval_result.holder_growth_score = 5

        # ── SOCIAL — TELEGRAM ACTIVITY (0-15 points) ─────────────────────
        tg_active = await self._check_telegram_active(tg_url) \
            if tg_url else False
        eval_result.telegram_active = tg_active

        if tg_active and has_twitter:
            eval_result.social_score = 15
        elif tg_active:
            eval_result.social_score = 12
        elif has_telegram and has_twitter:
            eval_result.social_score = 8  # Links but not confirmed active
        elif has_telegram or has_twitter:
            eval_result.social_score = 4
        else:
            eval_result.social_score = 0
            eval_result.warnings.append("No social presence")

        # ── LIQUIDITY (0-10 points) ───────────────────────────────────────
        eval_result.liquidity_ok = liquidity >= self.min_liquidity

        if liquidity >= 200_000:
            eval_result.liquidity_score = 10
        elif liquidity >= 100_000:
            eval_result.liquidity_score = 8
        elif liquidity >= self.min_liquidity:
            eval_result.liquidity_score = 5
        else:
            eval_result.liquidity_score = 0
            eval_result.warnings.append(
                f"Low liquidity ${liquidity:,.0f} (min ${self.min_liquidity:,.0f})"
            )

        # ── TOKEN AGE BONUS (+5 for sweet spot) ──────────────────────────
        if self.preferred_age_min <= age_hours <= self.preferred_age_max:
            eval_result.age_bonus = 5
        elif age_hours < self.preferred_age_min:
            eval_result.warnings.append(
                f"Very fresh ({age_hours:.1f}h) — initial dump not confirmed"
            )
        elif age_hours > self.preferred_age_max:
            eval_result.warnings.append(
                f"Older token ({age_hours:.0f}h) — check for new highs"
            )

        eval_result.calculate_total()

        logger.debug(
            f"[Evaluator] {token_symbol}: {eval_result.summary()}"
        )
        return eval_result

    def _build_candles(self, volume: dict, price_change: dict,
                        txns: dict, current_price: float) -> List[CandleData]:
        """Build candle objects from DexScreener period data."""
        periods = ["m5", "h1", "h6", "h24"]
        candles = []
        for period in periods:
            vol = volume.get(period, 0) or 0
            pc = price_change.get(period, 0) or 0
            tx = txns.get(period, {})
            buys = tx.get("buys", 0)
            sells = tx.get("sells", 0)

            # Estimate high/low from price change
            if pc > 0:
                high = current_price
                low = current_price / (1 + pc / 100)
            else:
                high = current_price * (1 + abs(pc) / 100)
                low = current_price

            candles.append(CandleData(
                period=period,
                volume_usd=vol,
                price_change_pct=pc,
                buy_count=buys,
                sell_count=sells,
                price_high=high,
                price_low=low
            ))
        return candles

    def _check_volume_acceleration(self,
                                    candles: List[CandleData]) -> tuple:
        """
        Volume accelerating = each shorter timeframe has
        proportionally more volume than the longer one.

        Check: m5 volume run-rate > h1 run-rate > h6 run-rate
        AND no sudden drops between periods.
        """
        if len(candles) < 3:
            return False, False

        # Annualize volumes to hourly rate for comparison
        vol_m5_hourly = candles[0].volume_usd * 12   # m5 × 12 = hourly
        vol_h1 = candles[1].volume_usd
        vol_h6_hourly = candles[2].volume_usd / 6    # h6 ÷ 6 = hourly

        # Check acceleration: current pace > recent pace > older pace
        accelerating = vol_m5_hourly > vol_h1 > vol_h6_hourly

        # Check for sudden drops (volume drop > 50% between any candles)
        has_drop = False
        vols = [vol_m5_hourly, vol_h1, vol_h6_hourly]
        for i in range(len(vols) - 1):
            if vols[i] > 0 and vols[i+1] > 0:
                if vols[i+1] < vols[i] * 0.50:
                    has_drop = True
                    break

        return accelerating, has_drop

    def _check_price_structure(self, candles: List[CandleData],
                                price_change: dict) -> tuple:
        """
        Check for Higher High AND Higher Low.
        Uses the available timeframe price change data.

        Higher High: price_change_h1 > 0 AND price_change_h6 > 0
                     (price is above where it was 1h ago AND 6h ago)
        Higher Low:  The low of the m5 candle is above the low of
                     the h1 candle (recent lows are rising)
        """
        pc_m5 = price_change.get("m5", 0) or 0
        pc_h1 = price_change.get("h1", 0) or 0
        pc_h6 = price_change.get("h6", 0) or 0

        # Higher High: consistent upward price across timeframes
        higher_high = pc_m5 > 0 and pc_h1 > 0 and pc_h6 > 0

        # Higher Low: recent candle low is above prior candle low
        # Approximate using the candle low estimates
        higher_low = False
        if len(candles) >= 2:
            recent_low = candles[0].price_low    # m5 low
            prior_low = candles[1].price_low     # h1 low
            higher_low = recent_low > prior_low

        return higher_high, higher_low

    async def _check_telegram_active(self, tg_url: str) -> bool:
        """
        Check if a Telegram group is active (not just a dead link).
        Fetches the Telegram page and looks for member count > threshold.
        """
        if not tg_url:
            return False

        try:
            username = tg_url.rstrip("/").split("/")[-1]
            if username.startswith("+"):
                # Private invite link — can't check member count
                return True  # Assume active if they have a private link

            url = f"https://t.me/{username}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5),
                    allow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0"}
                ) as resp:
                    if resp.status != 200:
                        return False
                    text = await resp.text()

                    # Look for member count in page
                    import re
                    patterns = [
                        r'(\d[\d\s,]*)\s*members?',
                        r'(\d[\d\s,]*)\s*subscribers?',
                        r'"members_count":(\d+)'
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, text, re.IGNORECASE)
                        if match:
                            count_str = match.group(1).replace(",", "").replace(" ", "")
                            try:
                                count = int(count_str)
                                # Active = more than 200 members
                                return count >= 200
                            except ValueError:
                                continue

                    # Page loaded but no member count found
                    # Check if it's a real group (not 404/error)
                    return "tgme_page_title" in text

        except Exception as e:
            logger.debug(f"[Evaluator] Telegram check error: {e}")
            return False

    def _estimate_age(self, pair_data: dict) -> float:
        """Estimate token age from pair creation time if available."""
        pair_created = pair_data.get("pairCreatedAt")
        if pair_created:
            try:
                created_ms = int(pair_created)
                created = datetime.fromtimestamp(
                    created_ms / 1000, tz=timezone.utc
                )
                age = datetime.now(timezone.utc) - created
                return age.total_seconds() / 3600
            except (ValueError, TypeError):
                pass

        # Fallback: estimate from price change pattern
        # If h24 change is very large, token is probably fresh
        pc_h24 = pair_data.get("priceChange", {}).get("h24", 0) or 0
        if abs(pc_h24) > 500:
            return 4.0  # Wild swings = very fresh
        elif abs(pc_h24) > 100:
            return 8.0
        else:
            return 16.0

    def get_stats(self) -> dict:
        return {
            "evaluations_run": self._evaluations_run,
            "hard_skips": self._hard_skips,
            "hard_skip_rate_pct": round(
                self._hard_skips / self._evaluations_run * 100, 1
            ) if self._evaluations_run > 0 else 0
        }
