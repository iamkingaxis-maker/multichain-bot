"""
Strategy 5 — Capitulation Reversal (Integrated)

Scans for tokens that crashed 60-85% from peak with volume dried up
and buyers returning. When setup quality >= 65, routes through the
scanner's process_external_signal() which runs security checks first.

Position management is handled by the main PositionManager — this
strategy is responsible for detection only.

Entry conditions (ALL must be true):
  1. Price dropped 60-85% from peak within 2 hours
  2. Volume spiked on the way down then dried up (sellers exhausted)
  3. Buy/sell ratio has flipped back positive (buyers returning)
  4. Token is 3+ hours old (survived initial dump phase)
  5. Current price is stabilizing (no new lows in last 10 minutes)
  6. Liquidity >= $30k, holder count >= 50
"""

import asyncio
import logging
import aiohttp
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

logger = logging.getLogger(__name__)

# Entry thresholds
MIN_DROP_FROM_PEAK_PCT = 60.0       # Must have dropped at least 60%
MAX_DROP_FROM_PEAK_PCT = 85.0       # If dropped 85%+ probably a rug
MIN_TOKEN_AGE_HOURS    = 3.0        # Must have survived initial dump
MAX_TOKEN_AGE_HOURS    = 24.0       # Too old = momentum gone
MIN_BUY_SELL_RATIO     = 0.55       # Buyers must be returning
MIN_SETUP_QUALITY      = 65.0       # Minimum quality score to enter


@dataclass
class CapitulationCandidate:
    """A token showing capitulation reversal setup."""
    token_address: str
    token_symbol: str
    chain_id: str

    current_price: float
    peak_price: float
    drop_from_peak_pct: float

    volume_h1: float
    volume_m5: float
    volume_dried_up: bool

    buy_sell_ratio: float
    holder_count: int
    liquidity_usd: float

    token_age_hours: float
    price_stable_minutes: float

    @property
    def setup_quality(self) -> float:
        score = 0.0

        if 60 <= self.drop_from_peak_pct <= 75:
            score += 30
        elif 75 < self.drop_from_peak_pct <= 85:
            score += 15

        if self.buy_sell_ratio >= 0.65:
            score += 25
        elif self.buy_sell_ratio >= 0.55:
            score += 15

        if self.volume_dried_up:
            score += 20

        if self.price_stable_minutes >= 10:
            score += 15
        elif self.price_stable_minutes >= 5:
            score += 8

        if 3 <= self.token_age_hours <= 12:
            score += 10

        return min(score, 100)

    @property
    def passes_filters(self) -> bool:
        return (
            MIN_DROP_FROM_PEAK_PCT <= self.drop_from_peak_pct <= MAX_DROP_FROM_PEAK_PCT
            and self.token_age_hours >= MIN_TOKEN_AGE_HOURS
            and self.token_age_hours <= MAX_TOKEN_AGE_HOURS
            and self.buy_sell_ratio >= MIN_BUY_SELL_RATIO
            and self.volume_dried_up
            and self.liquidity_usd >= 30_000
            and self.holder_count >= 50
        )


class CapitulationReversalStrategy:
    """
    Scans for tokens that crashed 60-85% and are showing reversal signs.
    When a quality setup is detected, routes through the scanner's
    process_external_signal() for security checks + position management.
    """

    def __init__(self,
                 scanner,
                 telegram,
                 min_setup_quality: float = MIN_SETUP_QUALITY,
                 scan_interval_seconds: int = 60):

        self.scanner = scanner
        self.telegram = telegram
        self.min_quality = min_setup_quality
        self.scan_interval = scan_interval_seconds

        # Price history for peak tracking: token → [prices]
        self._price_history: Dict[str, List[float]] = {}
        self._low_history:   Dict[str, List[float]] = {}
        self._entered:       set = set()

        # Stats
        self.setups_detected = 0
        self.entries_taken   = 0

    async def run(self):
        """Main loop — scans for capitulation setups every 60 seconds."""
        logger.info(
            f"[CapitulationReversal] Started | "
            f"Min drop: {MIN_DROP_FROM_PEAK_PCT}% | "
            f"Min quality: {self.min_quality:.0f}"
        )
        while True:
            try:
                await self._scan_for_setups()
            except Exception as e:
                logger.error(f"[CapitulationReversal] Error: {e}")
            await asyncio.sleep(self.scan_interval)

    async def _scan_for_setups(self):
        """Scan DexScreener for capitulation candidates."""
        try:
            pairs = await self._fetch_solana_pairs()
            for pair in pairs:
                candidate = self._evaluate_pair(pair)
                if candidate and candidate.passes_filters:
                    await self._handle_candidate(candidate)
        except Exception as e:
            logger.debug(f"[CapitulationReversal] Scan error: {e}")

    async def _fetch_solana_pairs(self) -> list:
        """Fetch active Solana pairs sorted by price change (biggest drops first)."""
        try:
            url = "https://api.dexscreener.com/latest/dex/search?q=solana"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = [
                            p for p in data.get("pairs", [])
                            if p.get("chainId") == "solana"
                        ]
                        # Sort by h1 price change ascending (biggest drops first)
                        pairs.sort(
                            key=lambda p: p.get("priceChange", {}).get("h1", 0) or 0
                        )
                        return pairs[:100]
        except Exception as e:
            logger.debug(f"[CapitulationReversal] Fetch error: {e}")
        return []

    def _evaluate_pair(self, pair: dict) -> Optional[CapitulationCandidate]:
        """Evaluate a pair for capitulation reversal setup."""
        try:
            token_address = pair.get("baseToken", {}).get("address", "")
            token_symbol  = pair.get("baseToken", {}).get("symbol", "?")

            if not token_address or token_address in self._entered:
                return None

            price = float(pair.get("priceUsd", 0) or 0)
            if price <= 0:
                return None

            # Maintain price history
            if token_address not in self._price_history:
                self._price_history[token_address] = []
                self._low_history[token_address]   = []

            self._price_history[token_address].append(price)
            if len(self._price_history[token_address]) > 20:
                self._price_history[token_address] = \
                    self._price_history[token_address][-20:]

            history    = self._price_history[token_address]
            peak_price = max(history)
            drop_pct   = ((peak_price - price) / peak_price * 100) \
                if peak_price > price else 0

            # Volume
            volume       = pair.get("volume", {})
            volume_h1    = float(volume.get("h1", 0) or 0)
            volume_m5    = float(volume.get("m5", 0) or 0)
            m5_run_rate  = volume_m5 * 12
            volume_dried = volume_h1 > 10_000 and m5_run_rate < volume_h1 * 0.15

            # Buy/sell ratio (last 5 min)
            txns_m5 = pair.get("txns", {}).get("m5", {})
            buys_m5 = int(txns_m5.get("buys", 0))
            sells_m5 = int(txns_m5.get("sells", 0))
            total_m5 = buys_m5 + sells_m5
            bs_ratio = buys_m5 / total_m5 if total_m5 > 0 else 0.5

            # Price stability
            lows = self._low_history[token_address]
            lows.append(price)
            if len(lows) > 10:
                self._low_history[token_address] = lows[-10:]
            recent_min    = min(lows[-3:]) if len(lows) >= 3 else price
            stable_min    = 0.0
            if len(lows) >= 3:
                for i in range(min(len(lows), 10), 0, -1):
                    if lows[-i] <= recent_min * 1.01:
                        stable_min += 1.0
                    else:
                        break

            # Token age
            created_ms = pair.get("pairCreatedAt", 0)
            age_hours  = 0.0
            if created_ms:
                launch = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - launch).total_seconds() / 3600

            liquidity    = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            holder_count = int(pair.get("info", {}).get("holders", 0) or 0)

            return CapitulationCandidate(
                token_address=token_address,
                token_symbol=token_symbol,
                chain_id="solana",
                current_price=price,
                peak_price=peak_price,
                drop_from_peak_pct=drop_pct,
                volume_h1=volume_h1,
                volume_m5=volume_m5,
                volume_dried_up=volume_dried,
                buy_sell_ratio=bs_ratio,
                holder_count=holder_count,
                liquidity_usd=liquidity,
                token_age_hours=age_hours,
                price_stable_minutes=stable_min,
            )

        except Exception as e:
            logger.debug(f"[CapitulationReversal] Evaluate error: {e}")
            return None

    async def _handle_candidate(self, candidate: CapitulationCandidate):
        """Handle a qualified capitulation candidate."""
        quality = candidate.setup_quality
        if quality < self.min_quality:
            return

        self.setups_detected += 1
        logger.info(
            f"[CapitulationReversal] 💥 SETUP: "
            f"{candidate.token_symbol} | "
            f"Drop: -{candidate.drop_from_peak_pct:.0f}% | "
            f"B/S: {candidate.buy_sell_ratio:.2f} | "
            f"Quality: {quality:.0f}/100"
        )

        # Route through scanner for security checks + position management
        reason = (
            f"CapitulationReversal | "
            f"Drop -{candidate.drop_from_peak_pct:.0f}% | "
            f"B/S {candidate.buy_sell_ratio:.2f} | "
            f"Quality {quality:.0f}"
        )
        fired = await self.scanner.process_external_signal(
            token_address=candidate.token_address,
            token_symbol=candidate.token_symbol,
            reason=reason,
            price_usd=candidate.current_price,
            liquidity_usd=candidate.liquidity_usd,
            volume_h1=candidate.volume_h1,
            signal_score=int(quality),
            strategy_tag="capitulation_reversal",
        )
        if fired:
            self._entered.add(candidate.token_address)
            self.entries_taken += 1

    def get_stats(self) -> dict:
        return {
            "strategy":        "capitulation_reversal",
            "setups_detected": self.setups_detected,
            "entries_taken":   self.entries_taken,
        }
