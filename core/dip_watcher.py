"""
DipWatcher — intercepts micro-cap buys and waits for a dip+recovery pattern
before placing the actual buy order.

Flow:
  1. Token passes all security/quality gates in the micro-cap path.
  2. Instead of buying immediately, the scanner calls dip_watcher.watch(...).
  3. DipWatcher subscribes the token to AxiomPriceFeed for real-time ticks.
  4. On each tick it tracks peak, bottom, and dip state.
  5. When a 15% dip is followed by a 5% recovery from the bottom → buy.
  6. If 5 minutes pass without a buy trigger → expire and skip.

Parameters (defaults match task spec):
  dip_threshold_pct = 15.0   — % drop from peak to declare "dipped"
  recovery_pct      =  5.0   — % recovery from bottom to trigger buy
  max_watch_seconds = 300    — expire after 5 minutes
"""

import asyncio
import logging
import time
from typing import Dict, Optional, Any

import aiohttp

logger = logging.getLogger(__name__)

_GECKO_API = "https://api.geckoterminal.com/api/v2"


class _WatchState:
    """Internal state for a single token being watched."""

    __slots__ = (
        "token_address", "token_symbol", "reason", "override_usd",
        "peak_price", "bottom_price", "dipped", "start_time",
        "signal_price", "h6_pct", "token_age_hours",
    )

    def __init__(self, token_address: str, token_symbol: str, reason: str,
                 override_usd: float, initial_price: float, start_time: float,
                 signal_price: float = 0.0, h6_pct: float = 0.0,
                 token_age_hours: float = 999.0):
        self.token_address  = token_address
        self.token_symbol   = token_symbol
        self.reason         = reason
        self.override_usd   = override_usd
        self.peak_price     = initial_price
        self.bottom_price   = initial_price
        self.dipped         = False
        self.start_time     = start_time
        self.signal_price   = signal_price
        self.h6_pct         = h6_pct
        self.token_age_hours = token_age_hours


class DipWatcher:
    """
    Watches tokens for a dip+recovery pattern before buying.

    Usage:
        dip_watcher = DipWatcher(price_feed=price_feed, trader=trader)
        # In scanner micro-cap path:
        await dip_watcher.watch(token_address, token_symbol, reason, override_usd)
    """

    def __init__(self,
                 price_feed,
                 trader,
                 dip_threshold_pct: float = 15.0,
                 recovery_pct: float = 5.0,
                 max_watch_seconds: float = 300.0):

        self.price_feed         = price_feed
        self.trader             = trader
        self.scanner            = None  # set by scanner after init via connect_to_scanner()
        self.dip_threshold_pct  = dip_threshold_pct
        self.recovery_pct       = recovery_pct
        self.max_watch_seconds  = max_watch_seconds

        # Active watches: token_address → _WatchState
        self._watches: Dict[str, _WatchState] = {}

        # Register ourselves as a price callback on the Axiom feed
        price_feed.register_price_callback(self.on_price_update)

        # Background task handle — polls DexScreener price cache every 1s
        # as a fallback for MC tokens that Axiom WS never delivers ticks for.
        self._dex_poll_task: Optional[Any] = None

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    async def watch(self,
                    token_address: str,
                    token_symbol: str,
                    reason: str,
                    override_usd: float,
                    signal_price: float = 0.0,
                    h6_pct: float = 0.0,
                    token_age_hours: float = 999.0):
        """
        Subscribe token to the price feed and start watching for dip+recovery.
        Called from the scanner micro-cap path instead of trader.buy().

        signal_price: DexScreener price at the moment the scanner fired — used as
        the reference for the synthetic-m1 check 15s later.
        """
        if token_address in self._watches:
            logger.debug(
                f"[DipWatcher] Already watching {token_symbol} — skipping duplicate"
            )
            return

        # Use last known price as the initial peak.
        # Try Axiom cache first, fall back to DexScreener cache.
        initial_price = self.price_feed.price_cache.get(token_address, 0.0)
        if initial_price <= 0:
            dex_feed = getattr(self.trader, "_dex_price_feed", None)
            if dex_feed is not None:
                initial_price = dex_feed.price_cache.get(token_address, 0.0)

        if initial_price <= 0:
            logger.info(
                f"[DipWatcher] Watching {token_symbol} ({token_address[:8]}…) — "
                f"no price yet, will set peak on first tick"
            )
        else:
            logger.info(
                f"[DipWatcher] Watching {token_symbol} ({token_address[:8]}…) | "
                f"initial price ${initial_price:.8f} | "
                f"dip_threshold={self.dip_threshold_pct}% | "
                f"recovery={self.recovery_pct}% | "
                f"max_watch={self.max_watch_seconds}s"
            )

        state = _WatchState(
            token_address=token_address,
            token_symbol=token_symbol,
            reason=reason,
            override_usd=override_usd,
            initial_price=initial_price,
            start_time=time.monotonic(),
            signal_price=signal_price,
            h6_pct=h6_pct,
            token_age_hours=token_age_hours,
        )
        self._watches[token_address] = state

        # Reserve this token on the trader — blocks other scanner paths from buying
        # while DipWatcher is waiting for dip+recovery.
        self.trader._dip_watching.add(token_address.lower())

        # Subscribe to real-time prices (safe to call if already subscribed)
        self.price_feed.subscribe_token(token_address)

        # Start the DexScreener price poll task if not already running.
        # This feeds 1s polling prices into on_price_update for tokens that
        # Axiom WS never delivers ticks for (most MC tokens).
        if self._dex_poll_task is None or self._dex_poll_task.done():
            self._dex_poll_task = asyncio.ensure_future(self._dex_price_poll())

        # Schedule the 30s check: verify momentum still positive before buying.
        asyncio.ensure_future(self._quick_mc_check(state))

    def on_price_update(self, token_address: str, price: float):
        """
        Called by AxiomPriceFeed on every price tick (synchronous, must be fast).
        Applies dip/recovery logic and schedules a buy when conditions are met.
        """
        state = self._watches.get(token_address)
        if state is None:
            return

        # First tick after watch registered with no initial price — set peak now
        if state.peak_price <= 0:
            state.peak_price  = price
            state.bottom_price = price
            logger.info(
                f"[DipWatcher] {state.token_symbol}: first tick — "
                f"peak set to ${price:.8f}"
            )
            return

        # Always track rising peak (before dip is confirmed)
        if not state.dipped and price > state.peak_price:
            state.peak_price = price

        if not state.dipped:
            # Check for dip
            drop_pct = (state.peak_price - price) / state.peak_price * 100.0
            if drop_pct >= self.dip_threshold_pct:
                state.dipped       = True
                state.bottom_price = price
                logger.info(
                    f"[DipWatcher] {state.token_symbol}: DIP DETECTED "
                    f"— peak ${state.peak_price:.8f} → "
                    f"${price:.8f} ({drop_pct:.1f}% drop)"
                )
        else:
            # We are in dip state — track lower bottom
            if price < state.bottom_price:
                state.bottom_price = price

            # Check for recovery
            recovery_pct = (price - state.bottom_price) / state.bottom_price * 100.0
            if recovery_pct >= self.recovery_pct:
                logger.info(
                    f"[DipWatcher] {state.token_symbol}: RECOVERY TRIGGERED "
                    f"— bottom ${state.bottom_price:.8f} → "
                    f"${price:.8f} ({recovery_pct:.1f}% recovery) — buying"
                )
                # Remove watch before scheduling buy to prevent double-trigger
                del self._watches[token_address]
                # Schedule buy as an asyncio task — do NOT await here (sync callback)
                asyncio.ensure_future(self._execute_buy(state, price))

    async def _dex_price_poll(self):
        """
        Poll DexScreener price cache every 1s for all watched tokens and feed
        prices into on_price_update. This is the primary price source for MC
        tokens because Axiom WS often doesn't deliver ticks for them
        (Axiom socket8 emits by pair address, not token address).
        """
        while self._watches:
            await asyncio.sleep(1)
            dex_feed = getattr(self.trader, "_dex_price_feed", None)
            if dex_feed is None:
                continue
            for token_address in list(self._watches.keys()):
                price = dex_feed.price_cache.get(token_address, 0.0)
                ts    = dex_feed.price_timestamps.get(token_address, 0.0)
                if price > 0 and (time.time() - ts) < 10:
                    self.on_price_update(token_address, price)

    @staticmethod
    def _required_buy_ratio(m5_change: float) -> float:
        """
        Minimum buy/sell ratio required, scaled by dip depth.
        Deeper dips need stronger reversal confirmation to avoid falling knives.
          -3% to -20%  → 35%  (light dip, buyers just need to be present)
          -20% to -40% → 50%  (moderate dip, majority must be buyers)
          -40% to -60% → 65%  (deep dip, strong demand reversal required)
        """
        if m5_change >= -20:
            return 0.35
        elif m5_change >= -40:
            return 0.50
        else:
            return 0.65

    async def _quick_mc_check(self, state: _WatchState):
        """
        30-second check: if real dip+recovery hasn't fired yet, verify the token
        still has positive momentum before buying via MC quick-buy path.

        Requires that price tracking actually happened (peak_price > 0).
        If no prices were received from any feed, we skip entirely — buying blind
        is consistently losing.
        """
        await asyncio.sleep(30)

        # Already triggered via dip+recovery path — nothing to do
        if state.token_address not in self._watches:
            return

        def _skip(reason):
            logger.info(
                f"[DipWatcher] MC SKIP: {state.token_symbol} "
                f"({state.token_address[:8]}…) — {reason}"
            )
            del self._watches[state.token_address]
            self.trader._dip_watching.discard(state.token_address.lower())
            self.price_feed.unsubscribe_token(state.token_address)

        current_price = 0.0
        m5_change = 0.0
        m1_change = None  # None = DexScreener did not return m1 data
        m5_buys = 0
        m5_sells = 0
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{state.token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    data = await resp.json(content_type=None)
                    pairs = [
                        p for p in (data.get("pairs") or [])
                        if p.get("chainId") == "solana"
                    ]
                    if pairs:
                        best = max(
                            pairs,
                            key=lambda p: (p.get("liquidity") or {}).get("usd") or 0,
                        )
                        current_price = float(best.get("priceUsd") or 0)
                        price_change = best.get("priceChange") or {}
                        m5_change = float(price_change.get("m5") or 0)
                        _m1_raw = price_change.get("m1")
                        m1_change = float(_m1_raw) if _m1_raw is not None else None
                        _m5_txns = (best.get("txns") or {}).get("m5") or {}
                        m5_buys = int(_m5_txns.get("buys") or 0)
                        m5_sells = int(_m5_txns.get("sells") or 0)
        except Exception as e:
            logger.debug(
                f"[DipWatcher] Quick MC check fetch failed for "
                f"{state.token_symbol}: {e}"
            )

        # Still not in watches? Dip+recovery fired in the meantime — exit
        if state.token_address not in self._watches:
            return

        if not current_price:
            _skip("DexScreener returned no price after 30s")
            return

        # Gate 1: not crashing from signal price (wider than before — 12% instead of 8%)
        if state.signal_price > 0:
            pct_from_signal = (current_price - state.signal_price) / state.signal_price * 100
            if pct_from_signal < -12:
                _skip(f"down {pct_from_signal:.1f}% from signal in 30s — reversing")
                return
        else:
            pct_from_signal = 0.0

        # Gate 1.5: m1 deceleration — if m1 data is available and deeply negative,
        # the dump is still accelerating right now. m5 is a 5-min average and lags;
        # m1 is what's happening this minute. Block immediately — the scaled buy_ratio
        # check in Gate 3 handles the no-m1 case.
        if m5_change <= -3 and m1_change is not None and m1_change < -15:
            _skip(
                f"m1={m1_change:+.1f}% < -15% — dump still accelerating "
                f"(m5={m5_change:+.1f}%), not yet stabilized"
            )
            return

        # Gate 2: require an actual dip — m5 must be in range (-3% to -60%).
        # Block pumping (m5 > 0), flat (-3% to 0%), or full crash (< -60%).
        if m5_change > 0:
            _skip(f"m5={m5_change:+.1f}% — no dip (positive momentum, not a dip entry)")
            return
        if m5_change > -3:
            _skip(f"m5={m5_change:+.1f}% — flat dead zone, no dip")
            return
        if m5_change < -60:
            _skip(f"m5={m5_change:+.1f}% — crash (< -60%), skipping")
            return

        # Deep dip zone (-40% to -60%): too early to call at 30s — token may still be
        # finding bottom. Schedule a 60s re-check to let it stabilize.
        if m5_change < -40:
            logger.info(
                f"[DipWatcher] Deep dip: {state.token_symbol} ({state.token_address[:8]}…) "
                f"m5={m5_change:+.1f}% — rescheduling for 60s re-check"
            )
            asyncio.ensure_future(self._second_chance_check(state))
            return

        # Gate 3: scaled buy/sell ratio + minimum 50 buyers.
        # Deeper dips require stronger reversal confirmation to filter falling knives.
        total_txns = m5_buys + m5_sells
        buy_ratio  = m5_buys / total_txns if total_txns > 0 else 0.0
        req_ratio  = self._required_buy_ratio(m5_change)
        if total_txns == 0:
            _skip(f"no txn data — cannot verify buyer activity")
            return
        if buy_ratio < req_ratio:
            _skip(
                f"buy_ratio={buy_ratio:.0%} ({m5_buys}b/{m5_sells}s) < "
                f"{req_ratio:.0%} required for m5={m5_change:+.1f}% dip"
            )
            return
        if m5_buys < 10:
            _skip(f"only {m5_buys} m5 buys — insufficient buyer activity (need 10+)")
            return

        # Dip confirmed — tell _chart_gate to allow red last candle
        state.dipped = True

        logger.info(
            f"[DipWatcher] MC BUY: {state.token_symbol} "
            f"({state.token_address[:8]}…) — "
            f"{pct_from_signal:+.1f}% from signal | m5={m5_change:+.1f}% | "
            f"m1={f'{m1_change:+.1f}%' if m1_change is not None else 'n/a'} | "
            f"{m5_buys} buyers | buy_ratio={buy_ratio:.0%} — buying"
        )

        del self._watches[state.token_address]
        asyncio.ensure_future(self._execute_buy(state, current_price))

    async def _second_chance_check(self, state: _WatchState):
        """
        60s re-check for tokens that were in deep dip (-40% to -60%) at the 30s gate.
        Waits another 30 seconds for the token to stabilize, then applies the same
        gates. No further retries — if it still fails here, skip it.
        """
        await asyncio.sleep(30)  # 30 more seconds = 60s total from signal

        if state.token_address not in self._watches:
            return  # triggered via real-time dip+recovery path already

        def _skip(reason):
            logger.info(
                f"[DipWatcher] MC SKIP (60s): {state.token_symbol} "
                f"({state.token_address[:8]}…) — {reason}"
            )
            del self._watches[state.token_address]
            self.trader._dip_watching.discard(state.token_address.lower())
            self.price_feed.unsubscribe_token(state.token_address)

        current_price = 0.0
        m5_change = 0.0
        m1_change = None
        m5_buys = 0
        m5_sells = 0
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{state.token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    data = await resp.json(content_type=None)
                    pairs = [
                        p for p in (data.get("pairs") or [])
                        if p.get("chainId") == "solana"
                    ]
                    if pairs:
                        best = max(
                            pairs,
                            key=lambda p: (p.get("liquidity") or {}).get("usd") or 0,
                        )
                        current_price = float(best.get("priceUsd") or 0)
                        price_change = best.get("priceChange") or {}
                        m5_change = float(price_change.get("m5") or 0)
                        _m1_raw = price_change.get("m1")
                        m1_change = float(_m1_raw) if _m1_raw is not None else None
                        _m5_txns = (best.get("txns") or {}).get("m5") or {}
                        m5_buys = int(_m5_txns.get("buys") or 0)
                        m5_sells = int(_m5_txns.get("sells") or 0)
        except Exception as e:
            logger.debug(
                f"[DipWatcher] 60s re-check fetch failed for {state.token_symbol}: {e}"
            )

        if state.token_address not in self._watches:
            return

        if not current_price:
            _skip("DexScreener returned no price after 60s")
            return

        # Gate 1: signal price check (same tolerance as 30s)
        if state.signal_price > 0:
            pct_from_signal = (current_price - state.signal_price) / state.signal_price * 100
            if pct_from_signal < -12:
                _skip(f"down {pct_from_signal:.1f}% from signal at 60s — still falling")
                return
        else:
            pct_from_signal = 0.0

        # Gate 1.5: m1 still accelerating down
        if m5_change <= -3 and m1_change is not None and m1_change < -15:
            _skip(
                f"m1={m1_change:+.1f}% < -15% at 60s — dump still accelerating "
                f"(m5={m5_change:+.1f}%)"
            )
            return

        # Gate 2: dip range — no further retry from here
        if m5_change > 0:
            _skip(f"m5={m5_change:+.1f}% — pumped past entry window at 60s")
            return
        if m5_change > -3:
            _skip(f"m5={m5_change:+.1f}% — flat at 60s, no dip")
            return
        if m5_change < -60:
            _skip(f"m5={m5_change:+.1f}% — still crashing at 60s, skipping")
            return

        # Gate 3: scaled buy_ratio + minimum 10 buyers (same thresholds as 30s check)
        total_txns = m5_buys + m5_sells
        buy_ratio  = m5_buys / total_txns if total_txns > 0 else 0.0
        req_ratio  = self._required_buy_ratio(m5_change)
        if total_txns == 0:
            _skip("no txn data at 60s — cannot verify buyer activity")
            return
        if buy_ratio < req_ratio:
            _skip(
                f"buy_ratio={buy_ratio:.0%} ({m5_buys}b/{m5_sells}s) < "
                f"{req_ratio:.0%} required for m5={m5_change:+.1f}% at 60s"
            )
            return
        if m5_buys < 10:
            _skip(f"only {m5_buys} m5 buys at 60s — insufficient activity (need 10+)")
            return

        state.dipped = True

        logger.info(
            f"[DipWatcher] MC BUY (60s): {state.token_symbol} "
            f"({state.token_address[:8]}…) — "
            f"{pct_from_signal:+.1f}% from signal | m5={m5_change:+.1f}% | "
            f"m1={f'{m1_change:+.1f}%' if m1_change is not None else 'n/a'} | "
            f"{m5_buys} buyers | buy_ratio={buy_ratio:.0%} — buying"
        )

        del self._watches[state.token_address]
        asyncio.ensure_future(self._execute_buy(state, current_price))

    async def _fetch_ohlcv_simple(self, token_address: str) -> Optional[list]:
        """
        Fetch up to 20 5-minute OHLCV candles from GeckoTerminal.
        Returns list of candles (oldest→newest) or None on failure.
        Each candle: [timestamp, open, high, low, close, volume]
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Step 1: resolve pool address from token
                pools_url = f"{_GECKO_API}/networks/solana/tokens/{token_address}/pools"
                async with session.get(
                    pools_url,
                    params={"page": "1"},
                    headers={"Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status != 200:
                        return None
                    pools_data = await resp.json()
                    pools = pools_data.get("data", [])
                    if not pools:
                        return None

                # Step 2: try each pool (up to 3) for candle data
                for pool in pools[:3]:
                    pool_addr = pool.get("attributes", {}).get("address", "")
                    if not pool_addr:
                        continue
                    ohlcv_url = f"{_GECKO_API}/networks/solana/pools/{pool_addr}/ohlcv/minute"
                    async with session.get(
                        ohlcv_url,
                        params={"aggregate": "5", "limit": "20", "currency": "usd"},
                        headers={"Accept": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=8),
                    ) as ohlcv_resp:
                        if ohlcv_resp.status != 200:
                            continue
                        ohlcv_data = await ohlcv_resp.json()
                        raw = (
                            ohlcv_data.get("data", {})
                            .get("attributes", {})
                            .get("ohlcv_list", [])
                        )
                        if raw:
                            return list(reversed(raw))  # oldest first
        except Exception:
            pass
        return None

    async def _chart_gate(self, state: _WatchState, trigger_price: float) -> bool:
        """
        Lightweight chart quality check before executing a DipWatcher buy.

        Gates (block if ANY fail):
          1. Last 5m candle must be green (recovery direction)
          2. No 5+ consecutive red 5m candles ending at current candle
          3. RSI(14) < 75 (not overbought)
          4. Price not >20% above VWAP (not chasing a pump)

        Fail-closed: if candles are unavailable or too few (<3), the buy is blocked.
        Without chart data we cannot confirm recovery direction or RSI — don't buy blind.
        Returns True to proceed, False to block.
        """
        candles = await self._fetch_ohlcv_simple(state.token_address)

        if not candles or len(candles) < 3:
            if state.dipped:
                # MC dip entry: _quick_mc_check already verified m5 ≤ -3%, 50+ buyers,
                # no crash. GeckoTerminal rarely indexes fresh MC tokens in time.
                # Allow through — the m5/buyer gate IS the dip confirmation.
                logger.info(
                    f"[DipWatcher] Chart gate PASS (no candles, dip confirmed by m5) — "
                    f"{state.token_symbol}: proceeding on _quick_mc_check evidence"
                )
                return True
            logger.info(
                f"[DipWatcher] Chart gate BLOCKED (no data) — {state.token_symbol}: "
                f"cannot verify recovery without candles"
            )
            return False  # fail-closed for non-dip entries

        # Gate 1: last candle direction.
        # For dip entries (state.dipped=True from _quick_mc_check or real dip+recovery),
        # skip this check — a red last candle is expected when buying a dip.
        last = candles[-1]
        last_open, last_close = float(last[1]), float(last[4])
        if last_close < last_open and not state.dipped:
            logger.info(
                f"[DipWatcher] Chart gate BLOCK — {state.token_symbol}: "
                f"last 5m candle is red (open={last_open:.8f} close={last_close:.8f})"
            )
            return False

        # Gate 2: consecutive red candles (last N before current)
        red_streak = 0
        for c in reversed(candles[:-1]):
            if float(c[4]) < float(c[1]):  # close < open
                red_streak += 1
            else:
                break
        if red_streak >= 4:  # 4 preceding reds + current green = token was dumping hard
            logger.info(
                f"[DipWatcher] Chart gate BLOCK — {state.token_symbol}: "
                f"{red_streak} consecutive red candles before current"
            )
            return False

        # Gate 3: RSI(14) < 75
        closes = [float(c[4]) for c in candles]
        if len(closes) >= 15:
            gains, losses = [], []
            for i in range(1, len(closes)):
                diff = closes[i] - closes[i - 1]
                gains.append(max(diff, 0.0))
                losses.append(max(-diff, 0.0))
            period = 14
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            if avg_loss > 0:
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
                if rsi > 75:
                    logger.info(
                        f"[DipWatcher] Chart gate BLOCK — {state.token_symbol}: "
                        f"RSI={rsi:.1f} > 75 (overbought)"
                    )
                    return False

        # Gate 4: price vs VWAP — block if chasing a pump (>20% above VWAP)
        try:
            total_vol = sum(float(c[5]) for c in candles)
            if total_vol > 0:
                vwap = sum(float(c[4]) * float(c[5]) for c in candles) / total_vol
                if vwap > 0 and trigger_price > vwap * 1.20:
                    logger.info(
                        f"[DipWatcher] Chart gate BLOCK — {state.token_symbol}: "
                        f"price ${trigger_price:.8f} is {((trigger_price/vwap)-1)*100:.1f}% above VWAP ${vwap:.8f}"
                    )
                    return False
        except Exception:
            pass

        logger.info(
            f"[DipWatcher] Chart gate PASS — {state.token_symbol}: "
            f"last_candle_green=True red_streak={red_streak} "
            f"trigger=${trigger_price:.8f}"
        )
        return True

    async def _execute_buy(self, state: _WatchState, trigger_price: float):
        """Execute the deferred buy after dip+recovery is confirmed."""
        # Always release reservation first so the trader slot is freed regardless of outcome
        self.trader._dip_watching.discard(state.token_address.lower())

        # Check scanner cooldowns — wrapped in try/except so any attribute error
        # never crashes the buy path.
        if self.scanner is not None:
            try:
                addr_lower = state.token_address.lower()
                _now = time.monotonic()
                _sl_expiry = getattr(self.scanner, "_sl_cooldown", {}).get(addr_lower, 0)
                if _sl_expiry > _now:
                    logger.info(
                        f"[DipWatcher] Loss cooldown block: {state.token_symbol} — "
                        f"{int(_sl_expiry - _now)}s remaining"
                    )
                    self.price_feed.unsubscribe_token(state.token_address)
                    return
                _pump_expiry = getattr(self.scanner, "_pump_cooldown", {}).get(addr_lower, 0)
                if _pump_expiry > _now:
                    logger.info(
                        f"[DipWatcher] Pump cooldown block: {state.token_symbol} — "
                        f"{int(_pump_expiry - _now)}s remaining"
                    )
                    self.price_feed.unsubscribe_token(state.token_address)
                    return
            except Exception as _cd_err:
                logger.debug(f"[DipWatcher] Cooldown check error (non-fatal): {_cd_err}")

        # Chart quality gate
        passed = await self._chart_gate(state, trigger_price)
        if not passed:
            logger.info(
                f"[DipWatcher] Buy BLOCKED by chart gate — {state.token_symbol} "
                f"({state.token_address[:8]}…)"
            )
            self.price_feed.unsubscribe_token(state.token_address)
            return

        try:
            if state.dipped and state.peak_price > 0 and state.bottom_price > 0:
                dip_pct = (state.peak_price - state.bottom_price) / state.peak_price * 100
                rec_pct = (trigger_price - state.bottom_price) / state.bottom_price * 100
                enriched_reason = (
                    f"{state.reason} | DipWatcher: dip+recovery "
                    f"(peak ${state.peak_price:.8f} → "
                    f"bottom ${state.bottom_price:.8f} [{dip_pct:.1f}% dip] → "
                    f"buy ${trigger_price:.8f} [{rec_pct:.1f}% recovery])"
                )
            elif state.peak_price > 0:
                enriched_reason = (
                    f"{state.reason} | DipWatcher: MC momentum "
                    f"(peak ${state.peak_price:.8f}, no dip → "
                    f"buy ${trigger_price:.8f})"
                )
            else:
                enriched_reason = (
                    f"{state.reason} | DipWatcher: MC momentum buy ${trigger_price:.8f}"
                )
            await self.trader.buy(
                token_address=state.token_address,
                token_symbol=state.token_symbol,
                reason=enriched_reason,
                signal_score=50,
                override_usd=state.override_usd,
            )
        except Exception as e:
            logger.error(
                f"[DipWatcher] Buy failed for {state.token_symbol}: {e}"
            )

    async def _expire_watches(self):
        """
        Background task — removes stale watches every 30 seconds.
        Tokens that haven't triggered a buy within max_watch_seconds are dropped.

        No-data tokens (Axiom feed never streamed a tick) are expired early after
        60s — we never buy blind on a timer as by that point the pump is over.
        """
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()

            # No-data expiry: Axiom feed never delivered a tick after 60s.
            # These tokens are too new/small to have price data — skip rather
            # than buy blind (buying into a stale pump is consistently losing).
            no_data = [
                addr for addr, state in list(self._watches.items())
                if state.peak_price <= 0 and (now - state.start_time) >= 60
            ]
            for addr in no_data:
                state = self._watches.pop(addr, None)
                if state:
                    elapsed = now - state.start_time
                    logger.info(
                        f"[DipWatcher] NO-DATA EXPIRE: {state.token_symbol} "
                        f"({addr[:8]}…) — Axiom feed silent after {elapsed:.0f}s — skipping"
                    )
                    self.trader._dip_watching.discard(addr.lower())
                    self.price_feed.unsubscribe_token(addr)

            expired = [
                addr for addr, state in list(self._watches.items())
                if (now - state.start_time) >= self.max_watch_seconds
            ]
            for addr in expired:
                state = self._watches.pop(addr, None)
                if state:
                    elapsed = now - state.start_time
                    logger.info(
                        f"[DipWatcher] EXPIRED: {state.token_symbol} "
                        f"({addr[:8]}…) — no buy trigger after "
                        f"{elapsed:.0f}s "
                        f"(dipped={state.dipped}, "
                        f"peak=${state.peak_price:.8f}, "
                        f"bottom=${state.bottom_price:.8f})"
                    )
                    self.trader._dip_watching.discard(addr.lower())
                    # Unsubscribe only if position manager is not already tracking it
                    # (price_feed.unsubscribe_token clears price_cache, which is safe here)
                    self.price_feed.unsubscribe_token(addr)

    def get_stats(self) -> dict:
        return {
            "active_watches": len(self._watches),
            "watched_tokens": [
                {
                    "symbol":   s.token_symbol,
                    "dipped":   s.dipped,
                    "peak":     s.peak_price,
                    "bottom":   s.bottom_price,
                    "elapsed_s": round(time.monotonic() - s.start_time, 1),
                }
                for s in self._watches.values()
            ],
        }
