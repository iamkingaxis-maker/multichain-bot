"""
Real-Time Signal Layer — Tick Pattern Detection + Order Book Scoring

Three classes that provide a 0-25 signal boost for tokens showing bullish
microstructure patterns:

  TickPatternDetector (0-15):  Higher lows + volume spike on reversal
  OrderBookScorer     (0-10):  Jupiter price impact asymmetry + Axiom buy ratio
  RealTimeSignalLayer (0-25):  Combines both, exposes watch/unwatch/score/run
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

WSOL_MINT = "So11111111111111111111111111111111111111112"
JUPITER_QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens"

# Ring buffer limits
MAX_TICKS = 500
ROLLING_WINDOW_SECS = 2 * 3600  # 2 hours

# DexScreener fetch interval per token
DEXSCREENER_FETCH_INTERVAL = 30.0

# OrderBookScorer cache TTL
OB_CACHE_TTL = 15.0


@dataclass
class Tick:
    price: float
    timestamp: float
    buy_count: int
    sell_count: int


# ─────────────────────────── TickPatternDetector ─────────────────────────────


class TickPatternDetector:
    """
    Background task that polls the position manager's _dex_volume_cache every
    0.5s and maintains per-token ring buffers for pattern detection.

    Scoring (0-15):
      - Higher lows (ascending local minima): 2 ascending → +5, 3+ → +10
      - Volume spike on reversal: m5 buys > 2x avg AND price > 10-tick MA → +5
      Total capped at 15.
    """

    def __init__(self, position_manager=None):
        self._pm = position_manager
        self._watched: set = set()                          # token addresses to track
        self._buffers: Dict[str, deque] = {}                # token_lower → deque[Tick]
        self._last_dex_fetch: Dict[str, float] = {}         # token_lower → monotonic ts
        self._dex_buy_sell: Dict[str, Tuple[int, int]] = {} # token_lower → (buys, sells)
        self._running = False

    def watch(self, token_address: str):
        addr = token_address.lower()
        self._watched.add(addr)
        if addr not in self._buffers:
            self._buffers[addr] = deque(maxlen=MAX_TICKS)

    def unwatch(self, token_address: str):
        addr = token_address.lower()
        self._watched.discard(addr)
        self._buffers.pop(addr, None)
        self._last_dex_fetch.pop(addr, None)
        self._dex_buy_sell.pop(addr, None)

    def get_pattern_score(self, token_address: str) -> int:
        addr = token_address.lower()
        buf = self._buffers.get(addr)
        if not buf or len(buf) < 5:
            return 0

        score = 0

        # ── Higher lows detection ──
        hl_score = self._higher_lows_score(buf)
        score += hl_score

        # ── Volume spike on reversal ──
        vs_score = self._volume_spike_score(addr, buf)
        score += vs_score

        return min(15, score)

    def _higher_lows_score(self, buf: deque) -> int:
        """Find local minima and check if they're ascending."""
        prices = [t.price for t in buf]
        minima = []
        for i in range(1, len(prices) - 1):
            if prices[i] < prices[i - 1] and prices[i] < prices[i + 1]:
                minima.append(prices[i])

        if len(minima) < 2:
            return 0

        # Check last 2-3 minima for ascending pattern
        recent = minima[-3:] if len(minima) >= 3 else minima[-2:]
        ascending = all(recent[i] < recent[i + 1] for i in range(len(recent) - 1))

        if not ascending:
            return 0

        if len(recent) >= 3:
            return 10  # 3+ ascending local minima
        return 5       # 2 ascending local minima

    def _volume_spike_score(self, addr: str, buf: deque) -> int:
        """Check if current m5 buy_count > 2x average of last 10 AND price > 10-tick MA."""
        # Collect buy_count observations from recent ticks
        buy_counts = [t.buy_count for t in buf if t.buy_count > 0]
        if len(buy_counts) < 3:
            return 0

        current_buys = buy_counts[-1]
        avg_buys = sum(buy_counts[-11:-1]) / max(len(buy_counts[-11:-1]), 1)

        if avg_buys <= 0 or current_buys <= 2 * avg_buys:
            return 0

        # Price must be above 10-tick moving average
        recent_prices = [t.price for t in buf][-10:]
        if not recent_prices:
            return 0
        ma = sum(recent_prices) / len(recent_prices)
        current_price = recent_prices[-1]

        if current_price > ma:
            return 5
        return 0

    async def run(self):
        """Background loop: poll price cache every 0.5s, fetch DexScreener every 30s."""
        self._running = True
        logger.info("[RTSignal/Tick] Pattern detector started")

        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[RTSignal/Tick] Poll error: {e}")
            await asyncio.sleep(0.5)

    async def _poll_once(self):
        now = time.monotonic()

        # Get the price cache from position manager
        price_cache = {}
        if self._pm and hasattr(self._pm, '_dex_volume_cache'):
            price_cache = self._pm._dex_volume_cache

        # Fetch DexScreener buy/sell for tokens that need it
        tokens_needing_dex = []
        for addr in list(self._watched):
            last_fetch = self._last_dex_fetch.get(addr, 0)
            if now - last_fetch >= DEXSCREENER_FETCH_INTERVAL:
                tokens_needing_dex.append(addr)

        if tokens_needing_dex:
            # Batch: fetch one at a time to avoid hammering
            addr = tokens_needing_dex[0]
            self._last_dex_fetch[addr] = now
            asyncio.create_task(self._fetch_dex_txns(addr))

        # Record ticks for all watched tokens
        for addr in list(self._watched):
            cached = price_cache.get(addr, {})
            price = cached.get("price", 0)
            if price <= 0:
                continue

            buys, sells = self._dex_buy_sell.get(addr, (0, 0))

            buf = self._buffers.get(addr)
            if buf is None:
                buf = deque(maxlen=MAX_TICKS)
                self._buffers[addr] = buf

            tick = Tick(
                price=price,
                timestamp=time.time(),
                buy_count=buys,
                sell_count=sells,
            )
            buf.append(tick)

            # Evict ticks outside the 2h rolling window
            cutoff = time.time() - ROLLING_WINDOW_SECS
            while buf and buf[0].timestamp < cutoff:
                buf.popleft()

    async def _fetch_dex_txns(self, token_address: str):
        """Fetch m5 buy/sell counts from DexScreener for a token."""
        try:
            url = f"{DEXSCREENER_TOKEN_URL}/{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200:
                        return
                    data = await r.json()
                    pairs = [p for p in data.get("pairs", [])
                             if p.get("chainId") == "solana"]
                    if not pairs:
                        return
                    pair = max(pairs,
                               key=lambda p: p.get("liquidity", {}).get("usd", 0))
                    txns = pair.get("txns", {}).get("m5", {})
                    buys = int(txns.get("buys", 0))
                    sells = int(txns.get("sells", 0))
                    self._dex_buy_sell[token_address.lower()] = (buys, sells)
        except Exception as e:
            logger.debug(f"[RTSignal/Tick] DexScreener fetch error: {e}")


# ─────────────────────────── OrderBookScorer ─────────────────────────────────


class OrderBookScorer:
    """
    On-demand scoring using Jupiter price impact + Axiom buy/sell volume.

    Jupiter (0-5):
      Buy impact < 1% AND sell impact > 2x buy → +5 (buy pressure)
      Roughly equal impact → +2
      Sell impact < buy → 0

    Axiom (0-5):
      buy_ratio > 0.65 → +5
      buy_ratio > 0.55 → +3
      else → 0

    Total: 0-10, cached 15s per token.
    """

    def __init__(self, axiom_price_feed=None):
        self._axiom_feed = axiom_price_feed
        self._cache: Dict[str, Tuple[int, float]] = {}  # token → (score, monotonic_ts)

    async def score(self, token_address: str, current_price: float) -> int:
        addr = token_address.lower()

        # Check cache
        cached = self._cache.get(addr)
        if cached and (time.monotonic() - cached[1]) < OB_CACHE_TTL:
            return cached[0]

        # Run Jupiter and Axiom concurrently
        jup_task = asyncio.create_task(self._jupiter_score(token_address, current_price))
        axiom_task = asyncio.create_task(self._axiom_score(token_address))

        try:
            results = await asyncio.wait_for(
                asyncio.gather(jup_task, axiom_task, return_exceptions=True),
                timeout=10.0
            )
        except asyncio.TimeoutError:
            results = [0, 0]

        jup_s = results[0] if isinstance(results[0], int) else 0
        axiom_s = results[1] if isinstance(results[1], int) else 0

        total = min(10, jup_s + axiom_s)
        self._cache[addr] = (total, time.monotonic())
        return total

    async def _jupiter_score(self, token_address: str, current_price: float) -> int:
        """Score based on Jupiter quote price impact asymmetry."""
        try:
            buy_amount = 100_000_000  # 0.1 SOL in lamports

            async with aiohttp.ClientSession() as session:
                # Buy quote: SOL → token
                buy_params = {
                    "inputMint": WSOL_MINT,
                    "outputMint": token_address,
                    "amount": str(buy_amount),
                }
                # Sell quote: token → SOL
                # Compute equivalent token amount from 0.1 SOL at current price
                # SOL price ~150 USD, so 0.1 SOL ~ $15 worth of tokens
                # We don't know exact SOL price here, but Jupiter handles conversion
                # Use the output amount from buy quote for sell
                buy_impact = None
                sell_impact = None

                try:
                    async with session.get(
                        JUPITER_QUOTE_URL,
                        params=buy_params,
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as r:
                        if r.status == 200:
                            data = await r.json()
                            buy_impact = float(data.get("priceImpactPct", 100))
                            out_amount = data.get("outAmount", "0")

                            # Now do sell quote with the output amount
                            sell_params = {
                                "inputMint": token_address,
                                "outputMint": WSOL_MINT,
                                "amount": str(out_amount),
                            }
                            async with session.get(
                                JUPITER_QUOTE_URL,
                                params=sell_params,
                                timeout=aiohttp.ClientTimeout(total=5)
                            ) as r2:
                                if r2.status == 200:
                                    data2 = await r2.json()
                                    sell_impact = float(data2.get("priceImpactPct", 100))
                except Exception:
                    pass

                if buy_impact is None or sell_impact is None:
                    return 0

                # Score: buy impact < 1% and sell impact > 2x buy → buy pressure
                buy_abs = abs(buy_impact)
                sell_abs = abs(sell_impact)

                if buy_abs < 1.0 and sell_abs > buy_abs * 2:
                    return 5
                elif buy_abs < 2.0 and sell_abs < buy_abs * 1.5:
                    return 2
                return 0

        except Exception as e:
            logger.debug(f"[RTSignal/OB] Jupiter error: {e}")
            return 0

    async def _axiom_score(self, token_address: str) -> int:
        """Score based on Axiom price feed volume data (buy ratio)."""
        try:
            if not self._axiom_feed:
                return 0

            # AxiomPriceFeed has volume_cache but no buy/sell breakdown.
            # It does have change_cache (price change %) — positive change can
            # serve as a weak proxy for buy dominance, but that's not buy_ratio.
            # Without explicit buy/sell volume from Axiom WebSocket, we score 0.
            #
            # However, if the Axiom feed's user_cache shows high active users
            # for this token, that's a bullish signal. Use user count as proxy.
            user_count = self._axiom_feed.user_cache.get(token_address, 0)
            if user_count >= 50:
                return 5
            elif user_count >= 20:
                return 3
            return 0

        except Exception as e:
            logger.debug(f"[RTSignal/OB] Axiom error: {e}")
            return 0


# ─────────────────────────── RealTimeSignalLayer ─────────────────────────────


class RealTimeSignalLayer:
    """
    Combines TickPatternDetector (0-15) and OrderBookScorer (0-10) for a
    0-25 real-time signal boost.

    Usage:
        layer = RealTimeSignalLayer(chain_name="Solana", position_manager=pm)
        layer.watch(token_address)
        score = layer.score(token_address, current_price)
        tasks.append(layer.run())
    """

    def __init__(self, chain_name: str = "Solana",
                 position_manager=None,
                 axiom_price_feed=None):
        self.chain_name = chain_name
        self.detector = TickPatternDetector(position_manager=position_manager)
        self.ob_scorer = OrderBookScorer(axiom_price_feed=axiom_price_feed)

    def watch(self, token_address: str):
        self.detector.watch(token_address)

    def unwatch(self, token_address: str):
        self.detector.unwatch(token_address)

    def score(self, token_address: str, current_price: float) -> int:
        """
        Synchronous pattern score + async order book score.
        Since this may be called from an async context, we try to get the
        OB score from cache; if not cached, return pattern score only and
        kick off an async OB fetch for next time.
        """
        pattern_score = self.detector.get_pattern_score(token_address)

        # Try to get cached OB score synchronously
        addr = token_address.lower()
        ob_score = 0
        cached = self.ob_scorer._cache.get(addr)
        if cached and (time.monotonic() - cached[1]) < OB_CACHE_TTL:
            ob_score = cached[0]
        else:
            # Schedule async OB score fetch for next evaluation
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.ob_scorer.score(token_address, current_price))
            except RuntimeError:
                pass  # No running loop — skip OB scoring

        total = min(25, pattern_score + ob_score)
        return total

    async def score_async(self, token_address: str, current_price: float) -> int:
        """Fully async version — waits for OB score."""
        pattern_score = self.detector.get_pattern_score(token_address)
        try:
            ob_score = await asyncio.wait_for(
                self.ob_scorer.score(token_address, current_price),
                timeout=10.0
            )
        except (asyncio.TimeoutError, Exception):
            ob_score = 0
        return min(25, pattern_score + ob_score)

    async def run(self):
        """Start the tick pattern detector background loop."""
        logger.info(f"[RTSignal/{self.chain_name}] Real-time signal layer started")
        await self.detector.run()
