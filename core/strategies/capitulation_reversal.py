"""
Strategy 5 — Capitulation Reversal (Integrated)

Scans for tokens that crashed 60-85% from peak with volume dried up
and buyers returning. When setup quality >= 65, routes through the
scanner's process_external_signal() which runs security checks first.

Position management is handled by the main PositionManager — this
strategy is responsible for detection only.

Entry conditions (ALL must be true):
  1. Price dropped 60-85% in the last 24h (uses DexScreener priceChange.h24)
  2. Volume spiked on the way down then dried up (sellers exhausted)
  3. Buy/sell ratio has flipped back positive (buyers returning)
  4. Token is 3+ hours old (survived initial dump phase)
  5. Current price is stabilizing (no new lows in last 10 minutes)
  6. Liquidity >= $30k, holder count >= 50

Data sources:
  Primary:   DexScreener (unauthenticated, priceChange.h24)
  Secondary: Axiom users-trending-v2?timePeriod=1h (authenticated via scanner)
             Returns most-active tokens in last hour — includes crashed ones.
"""

import asyncio
import logging
import aiohttp
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Entry thresholds
MIN_DROP_PCT    = 60.0       # Must have dropped at least 60% in 24h
MAX_DROP_PCT    = 85.0       # If dropped 85%+ probably a rug
MIN_TOKEN_AGE_HOURS = 3.0   # Must have survived initial dump
MAX_TOKEN_AGE_HOURS = 24.0  # Too old = momentum gone
MIN_BUY_SELL_RATIO  = 0.55  # Buyers must be returning
MIN_SETUP_QUALITY   = 65.0  # Minimum quality score to enter

# DexScreener endpoints that surface recently active Solana tokens
# Using multiple targeted queries to catch more crashed tokens
DEXSCREENER_QUERIES = [
    "https://api.dexscreener.com/latest/dex/search?q=pump.fun+solana",
    "https://api.dexscreener.com/latest/dex/search?q=solana+raydium",
    "https://api.dexscreener.com/latest/dex/search?q=solana+bonk.fun",
]


@dataclass
class CapitulationCandidate:
    """A token showing capitulation reversal setup."""
    token_address: str
    token_symbol: str
    chain_id: str

    current_price: float
    drop_pct_24h: float        # From DexScreener priceChange.h24 (positive = how much it dropped)
    drop_pct_6h: float         # From DexScreener priceChange.h6  (positive = how much it dropped)

    volume_h1: float
    volume_m5: float
    volume_dried_up: bool

    buy_sell_ratio: float
    holder_count: int
    liquidity_usd: float

    token_age_hours: float
    price_stable_minutes: float

    @property
    def drop_from_peak_pct(self) -> float:
        """Best estimate of drop from peak — prefers 6h over 24h if larger drop in 6h."""
        return max(self.drop_pct_24h, self.drop_pct_6h)

    @property
    def setup_quality(self) -> float:
        score = 0.0

        drop = self.drop_from_peak_pct
        if 60 <= drop <= 75:
            score += 30
        elif 75 < drop <= 85:
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
        drop = self.drop_from_peak_pct
        return (
            MIN_DROP_PCT <= drop <= MAX_DROP_PCT
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
    Uses DexScreener priceChange.h24/h6 for accurate drop data (no synthetic history).
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

        # Price stability tracking: token → [recent prices]
        self._low_history:          Dict[str, List[float]] = {}
        self._price_stable_since:   Dict[str, float] = {}   # token → unix ts when stability started
        self._entered:              set = set()
        self._entered_at:           Dict[str, float] = {}

        # Stats
        self.setups_detected = 0
        self.entries_taken   = 0

    async def run(self):
        """Main loop — scans for capitulation setups every 60 seconds."""
        logger.info(
            f"[CapitulationReversal] Started | "
            f"Min drop: {MIN_DROP_PCT}% | "
            f"Min quality: {self.min_quality:.0f}"
        )
        while True:
            try:
                await self._scan_for_setups()
            except Exception as e:
                logger.error(f"[CapitulationReversal] Error: {e}")
            await asyncio.sleep(self.scan_interval)

    async def _scan_for_setups(self):
        """Scan for capitulation candidates from multiple sources."""
        # Prune _low_history
        if len(self._low_history) > 500:
            for key in list(self._low_history.keys())[:-500]:
                del self._low_history[key]

        # Prune _entered — remove tokens added more than 24 hours ago
        now_mono = time.monotonic()
        expired = [t for t, ts in self._entered_at.items() if now_mono - ts > 86400]
        for t in expired:
            self._entered.discard(t)
            del self._entered_at[t]

        seen_addresses = set()
        candidates: List[CapitulationCandidate] = []

        # Source 1: Axiom users-trending-v2 (authenticated, best quality)
        axiom_pairs = await self._fetch_axiom_pairs()
        for pair in axiom_pairs:
            addr = pair.get("baseToken", {}).get("address", "")
            if addr and addr not in seen_addresses and addr not in self._entered:
                candidate = self._evaluate_pair(pair)
                if candidate and candidate.passes_filters:
                    candidates.append(candidate)
                    seen_addresses.add(addr)

        # Source 2: DexScreener (unauthenticated fallback / additional coverage)
        dex_pairs = await self._fetch_dexscreener_pairs()
        for pair in dex_pairs:
            addr = pair.get("baseToken", {}).get("address", "")
            if addr and addr not in seen_addresses and addr not in self._entered:
                candidate = self._evaluate_pair(pair)
                if candidate and candidate.passes_filters:
                    candidates.append(candidate)
                    seen_addresses.add(addr)

        for candidate in candidates:
            await self._handle_candidate(candidate)

    async def _fetch_axiom_pairs(self) -> list:
        """
        Fetch from Axiom users-trending-v2?timePeriod=1h.
        Uses the scanner's auth tokens. Returns empty list on any failure.
        """
        try:
            access_token = getattr(self.scanner, 'auth', None)
            if access_token is None:
                return []
            token = getattr(access_token, 'auth_token', None) or ""
            if not token:
                return []

            # Try each api server
            for server in ["https://api2.axiom.trade", "https://api3.axiom.trade",
                           "https://api4.axiom.trade"]:
                url = f"{server}/users-trending-v2?timePeriod=1h"
                headers = {
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "https://axiom.trade",
                    "Referer": "https://axiom.trade/",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Cookie": f"auth-access-token={token}",
                }
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            url, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=8)
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json(content_type=None)
                                pairs = data if isinstance(data, list) else data.get("pairs", [])
                                # Normalize to DexScreener-like format for _evaluate_pair
                                return self._normalize_axiom_pairs(pairs)
                            elif resp.status in (401, 403):
                                logger.debug("[CapitulationReversal] Axiom auth failed for trending fetch")
                                return []
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"[CapitulationReversal] Axiom fetch error: {e}")
        return []

    def _normalize_axiom_pairs(self, pairs: list) -> list:
        """
        Convert Axiom API pair format to the DexScreener-like format
        expected by _evaluate_pair(). Best-effort — drops unrecognized pairs.
        """
        out = []
        for p in pairs:
            try:
                # Axiom tokens can use different field names
                addr    = p.get("mint") or p.get("address") or p.get("tokenAddress") or ""
                symbol  = p.get("symbol") or p.get("ticker") or "?"
                price   = float(p.get("priceUsd") or p.get("price") or 0)
                liq     = float(p.get("liquidityUsd") or p.get("liquidity") or 0)
                mc      = float(p.get("marketCap") or 0)
                ch24    = float(p.get("priceChange24h") or p.get("change24h") or 0)
                ch6     = float(p.get("priceChange6h")  or p.get("change6h")  or 0)
                ch1     = float(p.get("priceChange1h")  or p.get("change1h")  or 0)
                vol_h1  = float(p.get("volumeH1")  or p.get("volume1h")  or 0)
                vol_m5  = float(p.get("volumeM5")  or p.get("volume5m")  or 0)
                buys_m5 = int(p.get("buysM5")   or p.get("buys5m")   or 0)
                sells_m5= int(p.get("sellsM5")  or p.get("sells5m")  or 0)
                created = int(p.get("pairCreatedAt") or p.get("createdAt") or 0)
                holders = int(p.get("holders") or 0)

                if not addr or price <= 0:
                    continue

                out.append({
                    "baseToken": {"address": addr, "symbol": symbol},
                    "chainId": "solana",
                    "priceUsd": str(price),
                    "liquidity": {"usd": liq},
                    "priceChange": {"h24": ch24, "h6": ch6, "h1": ch1},
                    "volume": {"h1": vol_h1, "m5": vol_m5},
                    "txns": {"m5": {"buys": buys_m5, "sells": sells_m5}},
                    "pairCreatedAt": created,
                    "info": {"holders": holders},
                })
            except Exception:
                continue
        return out

    async def _fetch_dexscreener_pairs(self) -> list:
        """
        Fetch Solana pairs from DexScreener using multiple queries.
        Combines results and deduplicates. Sorts by biggest 24h drop first.
        """
        all_pairs = []
        seen = set()

        for url in DEXSCREENER_QUERIES:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for p in data.get("pairs", []):
                                if p.get("chainId") != "solana":
                                    continue
                                addr = p.get("baseToken", {}).get("address", "")
                                if addr and addr not in seen:
                                    seen.add(addr)
                                    all_pairs.append(p)
            except Exception as e:
                logger.debug(f"[CapitulationReversal] DexScreener fetch error ({url}): {e}")

        # Sort by biggest 24h drop first (most negative h24 priceChange)
        all_pairs.sort(
            key=lambda p: p.get("priceChange", {}).get("h24", 0) or 0
        )
        return all_pairs[:150]

    def _evaluate_pair(self, pair: dict) -> Optional[CapitulationCandidate]:
        """Evaluate a pair for capitulation reversal setup using priceChange.h24/h6."""
        try:
            token_address = pair.get("baseToken", {}).get("address", "")
            token_symbol  = pair.get("baseToken", {}).get("symbol", "?")

            if not token_address or token_address in self._entered:
                return None

            price = float(pair.get("priceUsd", 0) or 0)
            if price <= 0:
                return None

            # --- Drop calculation: use DexScreener's authoritative price change ---
            price_changes = pair.get("priceChange", {})
            # priceChange.h24 is a signed percentage, e.g. -72.5 means dropped 72.5%
            raw_h24 = float(price_changes.get("h24", 0) or 0)
            raw_h6  = float(price_changes.get("h6",  0) or 0)
            drop_24h = abs(raw_h24) if raw_h24 < 0 else 0.0
            drop_6h  = abs(raw_h6)  if raw_h6  < 0 else 0.0

            # --- Volume analysis ---
            volume       = pair.get("volume", {})
            volume_h1    = float(volume.get("h1", 0) or 0)
            volume_m5    = float(volume.get("m5", 0) or 0)
            m5_run_rate  = volume_m5 * 12
            volume_dried = volume_h1 > 10_000 and m5_run_rate < volume_h1 * 0.15

            # --- Buy/sell ratio (last 5 min) ---
            txns_m5  = pair.get("txns", {}).get("m5", {})
            buys_m5  = int(txns_m5.get("buys",  0))
            sells_m5 = int(txns_m5.get("sells", 0))
            total_m5 = buys_m5 + sells_m5
            bs_ratio = buys_m5 / total_m5 if total_m5 > 0 else 0.5

            # --- Price stability (track recent price lows) ---
            if token_address not in self._low_history:
                self._low_history[token_address] = []
            lows = self._low_history[token_address]
            lows.append(price)
            if len(lows) > 10:
                self._low_history[token_address] = lows[-10:]
            lows = self._low_history[token_address]

            stable_min = 0.0
            if len(lows) >= 3:
                recent_min = min(lows[-3:])
                is_stable = price <= recent_min * 1.01
                now_ts = time.time()
                if is_stable:
                    if token_address not in self._price_stable_since:
                        self._price_stable_since[token_address] = now_ts
                    stable_min = (now_ts - self._price_stable_since[token_address]) / 60.0
                else:
                    self._price_stable_since.pop(token_address, None)

            # --- Token age ---
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
                drop_pct_24h=drop_24h,
                drop_pct_6h=drop_6h,
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
            f"[CapitulationReversal] SETUP: "
            f"{candidate.token_symbol} | "
            f"Drop: -{candidate.drop_from_peak_pct:.0f}% | "
            f"B/S: {candidate.buy_sell_ratio:.2f} | "
            f"Quality: {quality:.0f}/100"
        )

        # Mark entered BEFORE calling process_external_signal so we don't
        # re-evaluate this token on the next scan even if the trade is blocked.
        self._entered.add(candidate.token_address)
        self._entered_at[candidate.token_address] = time.monotonic()

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
            self.entries_taken += 1

    def get_stats(self) -> dict:
        return {
            "strategy":        "capitulation_reversal",
            "setups_detected": self.setups_detected,
            "entries_taken":   self.entries_taken,
        }
