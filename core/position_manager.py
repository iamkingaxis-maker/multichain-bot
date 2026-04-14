"""
Position Manager
Encodes the trader's exact rules for managing open positions.
"""

import asyncio
import logging
import aiohttp
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

COINGECKO_BTC = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"
DEXSCREENER_TOKEN = "https://api.dexscreener.com/latest/dex/tokens/"


@dataclass
class VolumeWindow:
    """30-minute volume observation."""
    volume_usd: float
    timestamp: datetime
    buy_count: int = 0
    sell_count: int = 0


@dataclass
class PositionState:
    """Full state for one open position."""
    token_address: str
    token_symbol: str
    chain_id: str
    entry_price: float
    entry_volume_usd: float    # h1 volume snapshot at entry — the baseline
    position_size_usd: float
    original_size_usd: float   # Before any average down
    entry_time: datetime

    # Buy reason — "micro" substring → is_micro_cap
    reason: str = ""
    is_micro_cap: bool = False

    # TP tracking
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False

    # Breakeven tracking
    breakeven_locked: bool = False

    # Stall tracking
    volume_windows: List[VolumeWindow] = field(default_factory=list)
    stall_exit_done: bool = False
    current_m5_volume: float = 0.0
    current_h1_volume: float = 0.0

    # Average down tracking
    averaged_down: bool = False
    avg_down_price: float = 0.0

    # Pyramid tracking
    pyramided: bool = False
    pyramid_signal_score: int = 0
    hh_hl_confirmed: bool = False

    # Liquidity tracking
    current_liquidity_usd: float = 0.0
    liquidity_confirmed: bool = True
    dead_liquidity_since: Optional[datetime] = None

    # Current state
    current_price: float = 0.0
    current_volume_usd: float = 0.0
    peak_price: float = 0.0
    min_price_usd: float = 0.0

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return ((self.current_price - self.entry_price) / self.entry_price) * 100

    @property
    def hours_open(self) -> float:
        return (datetime.now(timezone.utc) - self.entry_time).total_seconds() / 3600

    @property
    def stall_threshold(self) -> float:
        if self.entry_volume_usd >= 100_000:
            return 0.15
        elif self.entry_volume_usd >= 20_000:
            return 0.20
        else:
            return 0.30

    @property
    def is_stalled(self) -> bool:
        if self.entry_volume_usd <= 0:
            return False
        if not self.volume_windows:
            return False
        threshold = self.entry_volume_usd * self.stall_threshold
        m5_hourly_rate = self.current_m5_volume * 12
        m5_stalled = m5_hourly_rate < threshold
        h1_stalled = self.current_h1_volume < threshold
        price_not_rising = self.current_price <= self.peak_price * 0.99
        stalled = m5_stalled and h1_stalled and price_not_rising
        if stalled:
            logger.debug(
                f"[Stall] {self.token_symbol} | "
                f"m5×12: ${m5_hourly_rate:,.0f} | "
                f"h1: ${self.current_h1_volume:,.0f} | "
                f"Threshold: ${threshold:,.0f} "
                f"({self.stall_threshold*100:.0f}% of ${self.entry_volume_usd:,.0f}) | "
                f"Price {self.current_price:.6f} vs peak {self.peak_price:.6f}"
            )
        return stalled

    @property
    def volume_declining(self) -> bool:
        return self.is_stalled

    def add_volume_window(self, volume: float, buys: int = 0, sells: int = 0):
        self.volume_windows.append(VolumeWindow(
            volume_usd=volume,
            timestamp=datetime.now(timezone.utc),
            buy_count=buys,
            sell_count=sells
        ))
        if len(self.volume_windows) > 8:
            self.volume_windows = self.volume_windows[-8:]


class MarketConditionMonitor:
    def __init__(self,
                 btc_drop_threshold: float = 5.0,
                 restricted_score_threshold: int = 85,
                 override_score: int = 90,
                 normal_score_threshold: int = 65):
        self.btc_drop_threshold = btc_drop_threshold
        self.restricted_threshold = restricted_score_threshold
        self.override_score = override_score
        self.normal_threshold = normal_score_threshold

        self.btc_change_24h: float = 0.0
        self.market_restricted: bool = False
        self.restriction_reason: str = ""
        self.last_checked: Optional[datetime] = None

        self._on_restrict_callbacks: List[Callable] = []
        self._on_resume_callbacks: List[Callable] = []

    def on_restrict(self, cb: Callable):
        self._on_restrict_callbacks.append(cb)

    def on_resume(self, cb: Callable):
        self._on_resume_callbacks.append(cb)

    async def run(self):
        logger.info("[MarketMonitor] Started — watching BTC 24h change")
        while True:
            try:
                await self._check_conditions()
            except Exception as e:
                logger.debug(f"[MarketMonitor] Error: {e}")
            await asyncio.sleep(900)  # 15 minutes

    async def _check_conditions(self):
        change = await self._fetch_btc_change()
        if change is None:
            return
        self.btc_change_24h = change
        self.last_checked = datetime.now(timezone.utc)
        was_restricted = self.market_restricted
        if change <= -self.btc_drop_threshold:
            self.market_restricted = True
            self.restriction_reason = f"BTC {change:.1f}% 24h"
            if not was_restricted:
                for cb in self._on_restrict_callbacks:
                    try:
                        cb(self.restriction_reason)
                    except Exception:
                        pass
        else:
            self.market_restricted = False
            self.restriction_reason = ""
            if was_restricted:
                for cb in self._on_resume_callbacks:
                    try:
                        cb()
                    except Exception:
                        pass

    async def _fetch_btc_change(self) -> Optional[float]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    COINGECKO_BTC, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return float(data.get("bitcoin", {}).get("usd_24h_change", 0) or 0)
        except Exception:
            return None

    def get_current_threshold(self, signal_score: int = 0) -> int:
        if signal_score >= self.override_score:
            return self.override_score
        if self.market_restricted:
            return self.restricted_threshold
        return self.normal_threshold

    def should_trade(self, signal_score: int) -> bool:
        return signal_score >= self.get_current_threshold(signal_score)

    def get_stats(self) -> dict:
        return {
            "btc_24h_change": round(self.btc_change_24h, 2),
            "market_restricted": self.market_restricted,
            "restriction_reason": self.restriction_reason,
            "current_threshold": self.get_current_threshold(),
            "last_checked": self.last_checked.isoformat() if self.last_checked else None
        }


class PositionManager:
    """
    Manages all open positions with the trader's exact rules.
    Runs as a background task alongside the main trader.
    """

    def __init__(self,
                 chain_name: str,
                 chain_id: str,
                 trader,
                 open_positions_ref: dict,
                 telegram,
                 tracker,
                 market_monitor: MarketConditionMonitor,

                 # Standard take profit tiers
                 tp1_pct: float = 10.0,
                 tp1_sell: float = 1.0,
                 tp2_pct: float = 75.0,
                 tp2_sell: float = 0.40,
                 tp3_pct: float = 150.0,
                 tp3_sell: float = 1.0,

                 # Standard stop loss
                 stop_loss_pct: float = 7.0,

                 # Winner protection
                 winner_trail_pct: float = 15.0,

                 # Stall detection
                 stall_check_interval_min: int = 5,
                 stall_volume_threshold: float = 0.20,
                 stall_min_hours: float = 0.1,
                 stall_sell_pct: float = 1.0,

                 # Average down
                 avg_down_max_loss_pct: float = 4.0,
                 avg_down_min_volume_pct: float = 0.50,
                 avg_down_size_pct: float = 0.50,

                 # Micro-cap specific TP/SL
                 mc_tp1_pct: float = 10.0,
                 mc_tp1_sell: float = 1.0,
                 mc_tp2_pct: float = 75.0,
                 mc_tp2_sell: float = 0.40,
                 mc_tp3_pct: float = 200.0,
                 mc_tp3_sell: float = 1.0,
                 mc_stop_loss_pct: float = 25.0,
                 mc_winner_trail_pct: float = 15.0,

                 # Scalper reference — for breakeven-after-scalp check
                 scalper=None,
                 # Scanner reference — for stop-loss cooldown registration
                 scanner=None):

        self.chain_name = chain_name
        self.chain_id = chain_id
        self.trader = trader
        self.open_positions_ref = open_positions_ref
        self.telegram = telegram
        self.tracker = tracker
        self.market_monitor = market_monitor
        self.scanner = scanner

        # Standard TP settings
        self.tp1_pct = tp1_pct
        self.tp1_sell = tp1_sell
        self.tp2_pct = tp2_pct
        self.tp2_sell = tp2_sell
        self.tp3_pct = tp3_pct
        self.tp3_sell = tp3_sell

        # Standard SL
        self.stop_loss_pct = stop_loss_pct
        self.winner_trail_pct = winner_trail_pct

        # MC settings
        self.mc_tp1_pct = mc_tp1_pct
        self.mc_tp1_sell = mc_tp1_sell
        self.mc_tp2_pct = mc_tp2_pct
        self.mc_tp2_sell = mc_tp2_sell
        self.mc_tp3_pct = mc_tp3_pct
        self.mc_tp3_sell = mc_tp3_sell
        self.mc_stop_loss_pct = mc_stop_loss_pct
        self.mc_winner_trail_pct = mc_winner_trail_pct

        # Stall
        self.stall_interval = stall_check_interval_min
        self.stall_volume_threshold = stall_volume_threshold
        self.stall_min_hours = stall_min_hours
        self.stall_sell_pct = stall_sell_pct

        # Average down
        self.avg_down_max_loss = avg_down_max_loss_pct
        self.avg_down_min_volume = avg_down_min_volume_pct
        self.avg_down_size = avg_down_size_pct

        self.scalper = scalper

        self._states: Dict[str, PositionState] = {}
        self._last_volume_check: Dict[str, datetime] = {}
        self._stop_triggered: set = set()  # De-dup for realtime stops
        self._last_rest_fetch: Dict[str, float] = {}  # token → unix ts of last REST call
        self.axiom_price_feed = None  # Set by main.py — socket8 real-time cache (~ms latency)
        self.rpc_price_feed   = None  # Set by main.py — Solana RPC + Jupiter 0.5s poll
        self.dex_price_feed = None    # Set by main.py — DexScreener 1s poll cache (fallback)

        # Stats
        self.tp1_hits = 0
        self.tp2_hits = 0
        self.tp3_hits = 0
        self.stop_loss_hits = 0
        self.stall_exits = 0
        self.avg_downs = 0

    async def run(self):
        """Main position management loop — checks every 5 seconds (price from Axiom cache, REST throttled to 30s)."""
        logger.info(
            f"[PositionManager/{self.chain_name}] Started\n"
            f"  TP1: +{self.tp1_pct}% → sell {self.tp1_sell*100:.0f}%\n"
            f"  TP2: +{self.tp2_pct}% → sell {self.tp2_sell*100:.0f}% of remaining\n"
            f"  TP3: +{self.tp3_pct}% → sell {self.tp3_sell*100:.0f}% of remaining\n"
            f"  Stop: -{self.stop_loss_pct}% hard\n"
            f"  MC TP1: +{self.mc_tp1_pct}% → sell {self.mc_tp1_sell*100:.0f}%\n"
            f"  MC Stop: -{self.mc_stop_loss_pct}%\n"
            f"  Avg down: only if <{self.avg_down_max_loss}% loss + volume ok"
        )
        while True:
            try:
                await self._management_cycle()
            except Exception as e:
                logger.error(f"[PositionManager/{self.chain_name}] Error: {e}")
            await asyncio.sleep(5)

    async def _management_cycle(self):
        """One full management cycle across all open positions."""
        open_addrs = set(self.open_positions_ref.keys())

        # Remove closed positions
        for addr in list(self._states.keys()):
            if addr not in open_addrs:
                del self._states[addr]
                self._stop_triggered.discard(addr)

        # Initialize new positions
        for addr in open_addrs:
            if addr not in self._states:
                pos = self.open_positions_ref[addr]
                _reason = getattr(pos, "reason", "")
                _is_mc = "micro" in _reason.lower()
                entry_px = getattr(pos, "entry_price_usd", 0)
                self._states[addr] = PositionState(
                    token_address=addr,
                    token_symbol=getattr(pos, "token_symbol", "?"),
                    chain_id=self.chain_id,
                    entry_price=entry_px,
                    entry_volume_usd=0.0,
                    position_size_usd=getattr(pos, "amount_usd", 0),
                    original_size_usd=getattr(pos, "amount_usd", 0),
                    entry_time=getattr(pos, "entry_time", datetime.now(timezone.utc)),
                    reason=_reason,
                    is_micro_cap=_is_mc,
                    current_price=entry_px,
                    peak_price=entry_px,
                    min_price_usd=entry_px,
                    pyramid_signal_score=getattr(pos, "signal_score", 0),
                    hh_hl_confirmed=getattr(pos, "hh_hl_confirmed", False)
                )

        # Update prices and evaluate each position
        for addr, state in list(self._states.items()):
            await self._update_price(addr, state)
            if addr in self._states:
                await self._evaluate_position(addr, state)

    async def _fetch_jupiter_price(self, token_address: str) -> float:
        """
        Poll Jupiter Price API for near-instant AMM price.
        Much faster than DexScreener — queries the AMM state directly.
        Returns 0.0 on failure.
        """
        try:
            url = f"https://api.jup.ag/price/v2?ids={token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                    if resp.status != 200:
                        return 0.0
                    data = await resp.json()
                    item = (data.get("data") or {}).get(token_address) or (data.get("data") or {}).get(token_address.lower())
                    if item:
                        return float(item.get("price", 0) or 0)
        except Exception:
            pass
        return 0.0

    async def _update_price(self, token_address: str, state: PositionState):
        """
        Fetch current price via fastest available source:
          1. Axiom cache (<5s)  — socket8 WS, near real-time
          2. DexScreener cache (<3s) — PriceFeed polls every 1s
          3. Jupiter Price API — queries AMM directly, sub-second, throttled to 2s
          4. DexScreener REST — last resort, throttled to once per 5s
        Volume/liquidity for stall detection come from the REST call (throttled).
        """
        now_ts = time.time()
        addr_lower = token_address.lower()

        # ── Fast path 1: Axiom real-time cache ───────────────────────────────
        live_price = 0.0
        if self.axiom_price_feed is not None:
            _p = self.axiom_price_feed.price_cache.get(addr_lower, 0)
            _t = self.axiom_price_feed.price_timestamps.get(addr_lower, 0)
            if _p > 0 and (now_ts - _t) < 5.0:
                live_price = _p
                self._apply_price_update(
                    token_address, state, live_price,
                    volume_h1=self.axiom_price_feed.volume_cache.get(addr_lower, state.current_volume_usd),
                    volume_m5=state.current_m5_volume,
                    liquidity_usd=self.axiom_price_feed.liquidity_cache.get(addr_lower, state.current_liquidity_usd),
                )

        # ── Fast path 2: Solana RPC + Jupiter 0.5s-poll cache ────────────────
        # Covers all pool types: Pump.fun (direct RPC), PumpSwap, Raydium,
        # Meteora, Orca, LaunchLab (Jupiter API). ~1s latency vs 5-15s DexScreener.
        if live_price <= 0 and self.rpc_price_feed is not None:
            _p = self.rpc_price_feed.price_cache.get(addr_lower, 0)
            _t = self.rpc_price_feed.price_timestamps.get(addr_lower, 0)
            if _p > 0 and (now_ts - _t) < 2.0:
                live_price = _p
                self._apply_price_update(
                    token_address, state, live_price,
                    volume_h1=state.current_volume_usd,
                    volume_m5=state.current_m5_volume,
                    liquidity_usd=state.current_liquidity_usd,
                )

        # ── Fast path 3: DexScreener 1s-poll cache ───────────────────────────
        if live_price <= 0 and self.dex_price_feed is not None:
            _p = self.dex_price_feed.price_cache.get(addr_lower, 0)
            _t = self.dex_price_feed.price_timestamps.get(addr_lower, 0)
            if _p > 0 and (now_ts - _t) < 3.0:
                live_price = _p
                self._apply_price_update(
                    token_address, state, live_price,
                    volume_h1=self.dex_price_feed.volume_cache.get(addr_lower, state.current_volume_usd),
                    volume_m5=state.current_m5_volume,
                    liquidity_usd=self.dex_price_feed.liquidity_cache.get(addr_lower, state.current_liquidity_usd),
                )

        # ── Fast path 4: Jupiter Price API (throttled to 2s) ─────────────────
        # Fallback when RPC feed hasn't seen the token yet
        last_jup = self._last_rest_fetch.get(f"jup_{addr_lower}", 0)
        if live_price <= 0 and (now_ts - last_jup) >= 2.0:
            self._last_rest_fetch[f"jup_{addr_lower}"] = now_ts
            jup_price = await self._fetch_jupiter_price(token_address)
            if jup_price > 0:
                live_price = jup_price
                self._apply_price_update(token_address, state, live_price,
                                         volume_h1=state.current_volume_usd,
                                         volume_m5=state.current_m5_volume,
                                         liquidity_usd=state.current_liquidity_usd)

        # ── REST: volume/liquidity refresh (throttled to 5s) ─────────────────
        last_rest = self._last_rest_fetch.get(addr_lower, 0)
        if (now_ts - last_rest) < 5.0:
            return
        self._last_rest_fetch[addr_lower] = now_ts

        try:
            url = f"{DEXSCREENER_TOKEN}{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
                    pairs = [
                        p for p in data.get("pairs", [])
                        if p.get("chainId") == self.chain_id
                    ]
                    if not pairs:
                        return
                    pair = max(
                        pairs,
                        key=lambda p: p.get("liquidity", {}).get("usd", 0)
                    )
                    rest_price = float(pair.get("priceUsd", 0) or 0)
                    volume_data = pair.get("volume", {})
                    volume_h1 = volume_data.get("h1", 0) or 0
                    volume_m5 = volume_data.get("m5", 0) or 0
                    liquidity_usd = float(
                        (pair.get("liquidity") or {}).get("usd", 0) or 0
                    )

                    # Use REST price only if faster sources had nothing fresh
                    price_to_apply = live_price if live_price > 0 else rest_price
                    if price_to_apply > 0:
                        self._apply_price_update(token_address, state, price_to_apply,
                                                 volume_h1, volume_m5, liquidity_usd)

                    # Volume window for stall detection (uses REST data only)
                    last_check = self._last_volume_check.get(token_address)
                    if (not last_check or
                            (datetime.now(timezone.utc) - last_check).total_seconds()
                            >= self.stall_interval * 60):
                        state.add_volume_window(
                            volume_h1,
                            buys=pair.get("txns", {}).get("h1", {}).get("buys", 0),
                            sells=pair.get("txns", {}).get("h1", {}).get("sells", 0)
                        )
                        self._last_volume_check[token_address] = datetime.now(timezone.utc)

        except Exception as e:
            logger.debug(f"[PositionManager/{self.chain_name}] Price REST: {e}")

    def _apply_price_update(self, token_address: str, state: PositionState,
                             price: float, volume_h1: float,
                             volume_m5: float, liquidity_usd: float):
        """Apply a price update to state and sync back to the open position object."""
        state.current_price = price
        state.current_volume_usd = volume_h1
        state.current_h1_volume = volume_h1
        state.current_m5_volume = volume_m5
        state.current_liquidity_usd = liquidity_usd
        if price > state.peak_price:
            state.peak_price = price
        if state.min_price_usd <= 0 or price < state.min_price_usd:
            state.min_price_usd = price

        # Update liquidity confirmed flag
        MIN_EXIT_LIQUIDITY = 1000
        age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
        if (state.liquidity_confirmed
                and liquidity_usd < MIN_EXIT_LIQUIDITY
                and price <= 0          # only flag dead if price is also gone
                and age_seconds > 15):
            if state.dead_liquidity_since is None:
                state.dead_liquidity_since = datetime.now(timezone.utc)
        elif liquidity_usd >= MIN_EXIT_LIQUIDITY or price > 0:
            # Price > 0 means token is tradeable — bonding curve tokens show $0
            # LP liquidity on DexScreener but are actively priced via Jupiter/RPC.
            state.dead_liquidity_since = None

        # Sync live price/PnL back to the trader Position object
        if token_address in self.open_positions_ref:
            tp = self.open_positions_ref[token_address]
            tp.current_price_usd = price
            if state.entry_price > 0:
                tp.pnl_usd = (
                    (price / state.entry_price - 1)
                    * getattr(tp, "amount_usd", state.position_size_usd)
                )

        # Set entry volume baseline on first update
        if state.entry_volume_usd == 0 and volume_h1 > 0:
            state.entry_volume_usd = volume_h1
            logger.info(
                f"[PositionManager/{self.chain_name}] "
                f"📊 Baseline set: {state.token_symbol} "
                f"${volume_h1:,.0f}/hr — "
                f"stall threshold: "
                f"{state.stall_threshold*100:.0f}% "
                f"(${volume_h1 * state.stall_threshold:,.0f}/hr)"
            )

    async def _evaluate_position(self, token_address: str, state: PositionState):
        """Check all exit and management rules for one position."""
        if state.current_price <= 0 or state.entry_price <= 0:
            return

        MIN_EXIT_LIQUIDITY = 1000
        age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()

        # ── DEAD LIQUIDITY — write off if liquidity gone for >60s ────────────
        if (state.liquidity_confirmed
                and state.current_liquidity_usd < MIN_EXIT_LIQUIDITY
                and state.dead_liquidity_since is not None
                and age_seconds > 15):
            dead_seconds = (
                datetime.now(timezone.utc) - state.dead_liquidity_since
            ).total_seconds()
            # If price is still live, this is a DexScreener data gap — not a rug.
            # Pump.fun bonding curve tokens report $0 LP liquidity because their
            # liquidity lives in the bonding curve contract, not a traditional pool.
            # Real rugs kill price AND liquidity simultaneously.
            if state.current_price > 0:
                logger.warning(
                    f"[PositionManager/{self.chain_name}] ⚠️ Dead liquidity (price live): "
                    f"{state.token_symbol} — ${state.current_liquidity_usd:.0f} liq "
                    f"but price=${state.current_price:.8f} — likely bonding curve or "
                    f"pool migration, holding"
                )
                return
            logger.warning(
                f"[PositionManager/{self.chain_name}] ⚠️ Dead liquidity: "
                f"{state.token_symbol} — ${state.current_liquidity_usd:.0f} "
                f"(need ${MIN_EXIT_LIQUIDITY:,}) — waiting up to 60s"
            )
            if dead_seconds < 60:
                return  # Give it 60s to recover
            logger.warning(
                f"[PositionManager/{self.chain_name}] 💀 FULL LOSS (dead liquidity): "
                f"{state.token_symbol} — liquidity gone for "
                f"{dead_seconds/60:.1f} min — writing off ${state.position_size_usd:.0f}"
            )
            state.current_price = 0.0
            state.liquidity_confirmed = False
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason="Dead liquidity — full loss"
            )
            return

        # Recompute age for the rest of evaluation
        age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
        pnl_pct = state.pnl_pct

        # ═══════════════════════════════════════════════════════════════
        # MICRO-CAP POSITION MANAGEMENT
        # ═══════════════════════════════════════════════════════════════
        if state.is_micro_cap:

            # ── MC TIME STOP — exit if not positive at 10 minutes ────────
            if (not state.tp1_hit
                    and age_seconds >= 600
                    and pnl_pct <= 0):
                logger.warning(
                    f"[PositionManager/{self.chain_name}] ⏱ MC TIME STOP: "
                    f"{state.token_symbol} — not positive after "
                    f"{age_seconds/60:.0f}min ({pnl_pct:+.1f}%)"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"MC time stop — not positive at 10min"
                )
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=1800  # 30min cooldown for MC time stop
                    )
                return

            # ── MC WINNER TRAIL — close if drops mc_winner_trail_pct% from peak
            _MIN_PEAK_GAIN_FOR_TRAIL = 5.0
            _peak_gain_pct = (state.peak_price - state.entry_price) / state.entry_price * 100
            if (state.peak_price > 0
                    and _peak_gain_pct >= _MIN_PEAK_GAIN_FOR_TRAIL
                    and state.current_price <= state.peak_price * (1 - self.mc_winner_trail_pct / 100)):
                drop_from_peak = (state.peak_price - state.current_price) / state.peak_price * 100
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🔒 MC WINNER TRAIL: "
                    f"{state.token_symbol} -{drop_from_peak:.1f}% from peak"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"MC winner trail -{drop_from_peak:.1f}% from peak"
                )
                return

            # ── MC STOP LOSS ──────────────────────────────────────────────
            # Skip if realtime feed already claimed this position — prevents
            # a duplicate polling-loop sell racing against the realtime ensure_future.
            if token_address in self._stop_triggered:
                return
            if pnl_pct <= -self.mc_stop_loss_pct:
                age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
                is_flash_crash = age_seconds <= 120
                logger.warning(
                    f"[PositionManager/{self.chain_name}] 🛑 MC STOP LOSS: "
                    f"{state.token_symbol} at {pnl_pct:.1f}%"
                    + (" ⚠️ FLASH CRASH — possible rug" if is_flash_crash else "")
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"MC stop loss -{self.mc_stop_loss_pct:.0f}%"
                )
                self.stop_loss_hits += 1
                cooldown = 86400 if is_flash_crash else 14400
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=cooldown
                    )
                return

            # ── MC TAKE PROFIT TIERS ──────────────────────────────────────
            if pnl_pct >= self.mc_tp3_pct and not state.tp3_hit:
                state.tp3_hit = True
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🎯 MC TP3: "
                    f"{state.token_symbol} +{pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.mc_tp3_sell,
                    reason=f"MC TP3 +{pnl_pct:.1f}%"
                )
                return

            if pnl_pct >= self.mc_tp2_pct and not state.tp2_hit:
                state.tp2_hit = True
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🎯 MC TP2: "
                    f"{state.token_symbol} +{pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.mc_tp2_sell,
                    reason=f"MC TP2 +{pnl_pct:.1f}%"
                )
                return

            if pnl_pct >= self.mc_tp1_pct and not state.tp1_hit:
                state.tp1_hit = True
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🎯 MC TP1: "
                    f"{state.token_symbol} +{pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.mc_tp1_sell,
                    reason=f"MC TP1 +{pnl_pct:.1f}%"
                )
                return

            # MC stall check
            if (not state.stall_exit_done
                    and state.hours_open >= self.stall_min_hours
                    and state.is_stalled):
                m5_rate = state.current_m5_volume * 12
                threshold = state.entry_volume_usd * state.stall_threshold
                logger.info(
                    f"[PositionManager/{self.chain_name}] 😴 MC STALL: "
                    f"{state.token_symbol} | m5×12: ${m5_rate:,.0f} | "
                    f"Threshold: ${threshold:,.0f}"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.stall_sell_pct,
                    reason=f"Stall — m5×12 ${m5_rate:,.0f} both below ${threshold:,.0f}"
                )
                state.stall_exit_done = True
                self.stall_exits += 1
            return  # End MC path

        # ═══════════════════════════════════════════════════════════════
        # STANDARD POSITION MANAGEMENT
        # ═══════════════════════════════════════════════════════════════

        # ── WINNER PROTECTION — close 100% if drops winner_trail% from peak after TP1
        if (state.tp1_hit and
                state.peak_price > 0 and
                state.current_price <= state.peak_price * (1 - self.winner_trail_pct / 100)):
            drop_from_peak = (state.peak_price - state.current_price) / state.peak_price * 100
            logger.info(
                f"[PositionManager/{self.chain_name}] 🔒 WINNER TRAIL: "
                f"{state.token_symbol} -{drop_from_peak:.1f}% from peak"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Winner trail -{drop_from_peak:.1f}% from peak"
            )
            return

        # ── BREAKEVEN LOCK — once up 8%, protect at +3% ─────────────────
        # Trigger raised from 2%→8%: a 2% move is noise on volatile tokens,
        # not a real gain worth protecting. Floor raised from 0%→+3%: ensures
        # the exit actually covers slippage and nets a small profit.
        _BREAKEVEN_TRIGGER = 8.0   # lock after a real move
        _BREAKEVEN_FLOOR   = 3.0   # exit at +3% (covers slippage, nets small gain)
        if not state.breakeven_locked and pnl_pct >= _BREAKEVEN_TRIGGER:
            state.breakeven_locked = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🔒 BREAKEVEN LOCKED: "
                f"{state.token_symbol} at +{pnl_pct:.1f}% — "
                f"stop raised to +{_BREAKEVEN_FLOOR:.0f}%"
            )

        if state.breakeven_locked and not state.tp1_hit and pnl_pct <= _BREAKEVEN_FLOOR:
            logger.info(
                f"[PositionManager/{self.chain_name}] 🔒 BREAKEVEN EXIT: "
                f"{state.token_symbol} at {pnl_pct:+.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Breakeven exit {pnl_pct:+.1f}%"
            )
            if self.scanner:
                self.scanner.register_stop_loss(
                    token_address, state.token_symbol, state.current_price,
                    cooldown_seconds=7200  # 2h cooldown for breakeven exit
                )
            return

        # ── BREAKEVEN AFTER SCALP — if scalp has fired and price returns to entry ──
        if self.scalper is not None:
            scalp_state = self.scalper._states.get(token_address)
            if (scalp_state is not None and
                    (scalp_state.completed_cycles or scalp_state.active_cycle is not None) and
                    state.current_price <= state.entry_price):
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🔁 BREAKEVEN-AFTER-SCALP: "
                    f"{state.token_symbol} back at entry after scalp fired — closing 100%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason="Breakeven after scalp"
                )
                return

        # ── STOP LOSS — Hard stop with flash crash detection ──────────────
        # Skip if realtime feed already claimed this position — prevents
        # a duplicate polling-loop sell racing against the realtime ensure_future.
        if token_address in self._stop_triggered:
            return
        if pnl_pct <= -self.stop_loss_pct:
            age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
            is_flash_crash = age_seconds <= 120  # stop-loss in ≤2 minutes = likely rug
            logger.warning(
                f"[PositionManager/{self.chain_name}] 🛑 STOP LOSS: "
                f"{state.token_symbol} at {pnl_pct:.1f}%"
                + (" ⚠️ FLASH CRASH — possible rug" if is_flash_crash else "")
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Stop loss -{self.stop_loss_pct}%"
            )
            self.stop_loss_hits += 1
            cooldown = 86400 if is_flash_crash else 14400  # 24h on rug, 4h on normal stop
            if self.scanner:
                self.scanner.register_stop_loss(
                    token_address, state.token_symbol, state.current_price,
                    cooldown_seconds=cooldown
                )
            return

        # ── EARLY MOMENTUM FAILURE EXIT — bail at 10min if no momentum ────
        # Winners appear within 15min. Anything still down at 10min is a bad entry.
        if (not state.tp1_hit
                and age_seconds >= 600
                and pnl_pct <= -3.0):
            logger.info(
                f"[PositionManager/{self.chain_name}] ⏱ EARLY EXIT: "
                f"{state.token_symbol} {pnl_pct:+.1f}% at {age_seconds/60:.0f}min — no momentum"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Early exit {pnl_pct:.1f}% — no momentum at 10min"
            )
            if self.scanner:
                self.scanner.register_stop_loss(
                    token_address, state.token_symbol, state.current_price,
                    cooldown_seconds=7200  # 2h cooldown — bad entry, not rug
                )
            return

        # ── TAKE PROFIT TIERS ────────────────────────────────────────────
        if pnl_pct >= self.tp3_pct and not state.tp3_hit:
            state.tp3_hit = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 TP3: "
                f"{state.token_symbol} +{pnl_pct:.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.tp3_sell,
                reason=f"TP3 +{pnl_pct:.1f}%"
            )
            self.tp3_hits += 1
            return

        if pnl_pct >= self.tp2_pct and not state.tp2_hit:
            state.tp2_hit = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 TP2: "
                f"{state.token_symbol} +{pnl_pct:.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.tp2_sell,
                reason=f"TP2 +{pnl_pct:.1f}%"
            )
            self.tp2_hits += 1
            return

        if pnl_pct >= self.tp1_pct and not state.tp1_hit:
            state.tp1_hit = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 TP1: "
                f"{state.token_symbol} +{pnl_pct:.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.tp1_sell,
                reason=f"TP1 +{pnl_pct:.1f}%"
            )
            self.tp1_hits += 1

            # Pyramid if momentum is still healthy — press winning trades.
            # Score is no longer used as a gating mechanism, so replace with
            # real-time volume check: only add if volume hasn't stalled yet.
            # Skip micro-caps — too volatile for a second tranche.
            if (not state.pyramided and
                    not state.is_micro_cap and
                    not state.is_stalled):
                await self._execute_pyramid(token_address, state, pnl_pct)
            return

        # ── STALL DETECTION ──────────────────────────────────────────────
        if (not state.stall_exit_done and
                state.hours_open >= self.stall_min_hours and
                state.is_stalled):
            m5_rate = state.current_m5_volume * 12
            threshold = state.entry_volume_usd * state.stall_threshold
            tier = (
                "High entry" if state.entry_volume_usd >= 100_000
                else "Medium entry" if state.entry_volume_usd >= 20_000
                else "Low entry"
            )
            logger.info(
                f"[PositionManager/{self.chain_name}] 😴 STALL: "
                f"{state.token_symbol} | "
                f"m5×12: ${m5_rate:,.0f} | "
                f"h1: ${state.current_h1_volume:,.0f} | "
                f"Threshold: ${threshold:,.0f} ({state.stall_threshold*100:.0f}%) | "
                f"Tier: {tier}"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.stall_sell_pct,
                reason=(
                    f"Stall — m5×12 ${m5_rate:,.0f} + "
                    f"h1 ${state.current_h1_volume:,.0f} "
                    f"both below ${threshold:,.0f}"
                )
            )
            state.stall_exit_done = True
            self.stall_exits += 1
            await self.telegram.send(
                f"😴 *Stall Exit* [{self.chain_name}]\n\n"
                f"🪙 ${state.token_symbol}\n"
                f"📊 PnL: {pnl_pct:+.1f}%\n"
                f"📉 m5 run-rate: ${m5_rate:,.0f}/hr\n"
                f"📉 h1 volume: ${state.current_h1_volume:,.0f}/hr\n"
                f"🎯 Threshold: ${threshold:,.0f}/hr "
                f"({state.stall_threshold*100:.0f}% — {tier})\n"
                f"✅ Sold {self.stall_sell_pct*100:.0f}%\n"
                f"⏱ Open: {state.hours_open:.1f}h"
            )
            return

        # ── AVERAGE DOWN ─────────────────────────────────────────────────
        if (not state.averaged_down and
                state.hours_open >= (5 / 60) and
                -self.avg_down_max_loss <= pnl_pct < 0):
            vol_ratio = (
                state.current_volume_usd / state.entry_volume_usd
                if state.entry_volume_usd > 0 else 0
            )
            if vol_ratio >= self.avg_down_min_volume:
                logger.info(
                    f"[PositionManager/{self.chain_name}] 📉 AVG DOWN: "
                    f"{state.token_symbol} at {pnl_pct:.1f}% | "
                    f"Volume: {vol_ratio:.1f}x entry | "
                    f"Adding {self.avg_down_size*100:.0f}% more"
                )
                add_usd = state.original_size_usd * self.avg_down_size
                await self.trader.buy(
                    token_address=token_address,
                    token_symbol=state.token_symbol,
                    reason=(
                        f"Avg down at {pnl_pct:.1f}% — "
                        f"volume still {vol_ratio:.1f}x entry"
                    )
                )
                state.averaged_down = True
                state.avg_down_price = state.current_price
                self.avg_downs += 1
                await self.telegram.send(
                    f"📉 *Averaged Down* [{self.chain_name}]\n\n"
                    f"🪙 ${state.token_symbol}\n"
                    f"📊 Loss: {pnl_pct:.1f}% (within -{self.avg_down_max_loss}% limit)\n"
                    f"💧 Volume: {vol_ratio:.1f}x entry (still healthy)\n"
                    f"💰 Added: ${add_usd:.0f} (50% of original)\n"
                    f"⚠️ One time only — no second avg down"
                )

    def check_stop_loss_realtime(self, token_address: str, price_usd: float):
        """
        Called synchronously from the Axiom price feed on every price tick.
        Fires stop loss immediately via asyncio.ensure_future() rather than
        waiting up to 30 seconds for the poll cycle to notice the breach.
        """
        token_address = token_address.lower()
        state = self._states.get(token_address)
        if not state or state.entry_price <= 0:
            return
        if token_address in self._stop_triggered:
            return

        age_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
        if age_seconds < 5:
            return  # Ignore first 5s — entry price settling

        pnl_pct = (price_usd / state.entry_price - 1) * 100
        stop_pct = self.mc_stop_loss_pct if state.is_micro_cap else self.stop_loss_pct

        if pnl_pct <= -stop_pct:
            self._stop_triggered.add(token_address)
            state.current_price = price_usd
            label = (
                f"MC stop loss -{stop_pct:.0f}% [realtime]"
                if state.is_micro_cap else
                f"Stop loss -{stop_pct:.0f}% [realtime]"
            )
            logger.warning(
                f"[PositionManager/{self.chain_name}] ⚡ REALTIME STOP: "
                f"{state.token_symbol} at {pnl_pct:.1f}% — scheduling immediate sell"
            )
            # Flash crash detection: ≤ 120s hold = likely rug → 24h cooldown
            is_flash_crash = age_seconds <= 120
            cooldown = 86400 if is_flash_crash else 7200  # 24h rug, 2h normal realtime stop
            if self.scanner:
                self.scanner.register_stop_loss(
                    token_address, state.token_symbol, price_usd,
                    cooldown_seconds=cooldown
                )

            async def _do_realtime_stop(addr, st, lbl):
                try:
                    await self._execute_sell(addr, st, pct=1.0, reason=lbl)
                    self.stop_loss_hits += 1
                except Exception as e:
                    logger.error(
                        f"[PositionManager/{self.chain_name}] ⚡ Realtime stop sell failed for "
                        f"{st.token_symbol}: {e} — clearing trigger for retry"
                    )
                    self._stop_triggered.discard(addr)

            asyncio.ensure_future(_do_realtime_stop(token_address, state, label))

    async def _execute_sell(self, token_address: str,
                             state: PositionState,
                             pct: float, reason: str):
        """Execute a sell through the main trader."""
        try:
            await self.trader.sell(
                token_address=token_address,
                token_symbol=state.token_symbol,
                reason=reason,
                pct=pct
            )
            if pct >= 1.0 and token_address in self._states:
                del self._states[token_address]
                self._stop_triggered.discard(token_address)
        except Exception as e:
            logger.error(
                f"[PositionManager/{self.chain_name}] Sell error: {e}"
            )
            raise  # re-raise so callers (e.g. _do_realtime_stop) can handle retry

    async def _execute_pyramid(self, token_address: str,
                               state, pnl_pct: float):
        """Add 50% of original position size after TP1 when volume still healthy."""
        try:
            add_usd = state.original_size_usd * 0.50
            logger.info(
                f"[PositionManager/{self.chain_name}] 📈 PYRAMID: "
                f"{state.token_symbol} +{pnl_pct:.1f}% — "
                f"vol healthy, adding ${add_usd:.0f} (50% of original)"
            )
            await self.trader.buy(
                token_address=token_address,
                token_symbol=f"{state.token_symbol}[PYRAMID]",
                reason=f"Pyramid TP1 +{pnl_pct:.1f}% — volume healthy",
                override_usd=add_usd,
            )
            state.pyramided = True
            await self.telegram.send(
                f"📈 *Pyramid* [{self.chain_name}]\n\n"
                f"🪙 ${state.token_symbol}\n"
                f"📊 PnL: +{pnl_pct:.1f}%\n"
                f"💰 Adding: ${add_usd:.0f} (50% of original)\n"
                f"💧 Volume: healthy at TP1\n"
                f"⚠️ One tranche only — no second pyramid"
            )
        except Exception as e:
            logger.error(f"[PositionManager] Pyramid error: {e}")

    def get_stats(self) -> dict:
        return {
            "chain": self.chain_name,
            "open_positions": len(self._states),
            "tp1_hits": self.tp1_hits,
            "tp2_hits": self.tp2_hits,
            "tp3_hits": self.tp3_hits,
            "stop_loss_hits": self.stop_loss_hits,
            "stall_exits": self.stall_exits,
            "avg_downs": self.avg_downs
        }

    def get_position_detail(self, token_address: str) -> Optional[dict]:
        state = self._states.get(token_address)
        if not state:
            return None
        return {
            "symbol": state.token_symbol,
            "pnl_pct": round(state.pnl_pct, 2),
            "hours_open": round(state.hours_open, 1),
            "tp1_hit": state.tp1_hit,
            "tp2_hit": state.tp2_hit,
            "tp3_hit": state.tp3_hit,
            "averaged_down": state.averaged_down,
            "stall_exit_done": state.stall_exit_done,
            "volume_declining": state.volume_declining,
            "current_price": state.current_price,
            "peak_price": state.peak_price,
            "is_micro_cap": state.is_micro_cap,
            "breakeven_locked": state.breakeven_locked
        }
