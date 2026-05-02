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
    strategy: str = "scanner"  # "graduation" gets wider stop loss

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

    # Transaction rate tracking (h1 buys+sells from DexScreener REST)
    entry_txns_h1: int = 0    # baseline set on first REST update
    current_txns_h1: int = 0  # refreshed every 5s REST poll

    # Current state
    current_price: float = 0.0
    current_volume_usd: float = 0.0
    peak_price: float = 0.0
    min_price_usd: float = 0.0

    # Scalp 4-phase detector metadata: sweep_low, stop_price, tp1_price, entry_close_time, pool_address
    scalp_meta: Optional[dict] = None

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

                 # Dip buy specific TP/SL — defaults match utils/config.py
                 # so test/script instantiations without explicit kwargs see
                 # production behavior. main.py still passes config.* values
                 # explicitly at startup.
                 dip_tp1_pct: float = 8.0,
                 dip_tp1_sell: float = 0.50,
                 dip_tp2_pct: float = 15.0,
                 dip_tp2_sell: float = 1.0,
                 dip_stop_pct: float = 15.0,
                 dip_winner_trail_pct: float = 3.5,

                 # Scalp strategy TP/SL (4-phase rewrite)
                 scalp_tp1_pct: float = 10.0,
                 scalp_tp1_sell: float = 0.50,
                 scalp_tp2_pct: float = 15.0,
                 scalp_tp2_sell: float = 0.35,
                 scalp_stop_pct: float = 6.0,
                 scalp_time_exit_candles: int = 4,
                 scalp_time_exit_min_pct: float = 5.0,
                 scalp_max_hold_minutes: float = 45.0,

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

        # Dip buy settings
        self.dip_tp1_pct = dip_tp1_pct
        self.dip_tp1_sell = dip_tp1_sell
        self.dip_tp2_pct = dip_tp2_pct
        self.dip_tp2_sell = dip_tp2_sell
        self.dip_stop_pct = dip_stop_pct
        self.dip_winner_trail_pct = dip_winner_trail_pct

        # Scalp (4-phase rewrite)
        self.scalp_tp1_pct = scalp_tp1_pct
        self.scalp_tp1_sell = scalp_tp1_sell
        self.scalp_tp2_pct = scalp_tp2_pct
        self.scalp_tp2_sell = scalp_tp2_sell
        self.scalp_stop_pct = scalp_stop_pct
        self.scalp_time_exit_candles = scalp_time_exit_candles
        self.scalp_time_exit_min_pct = scalp_time_exit_min_pct
        self.scalp_max_hold_minutes = scalp_max_hold_minutes
        self.scalp_queue = None  # set by main.py after construction

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
        # Last realtime price per token — used as the reference for the
        # downside sanity gate (reject ticks that drop >20% from prior).
        # Catches single corrupted feed ticks that fire spurious -15% stops
        # then recover (lifetime: ~55% of stops realized at <-14.5%).
        self._last_realtime_price: dict = {}
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
                self._last_realtime_price.pop(addr, None)

        # Initialize new positions
        for addr in open_addrs:
            if addr not in self._states:
                pos = self.open_positions_ref[addr]
                _reason = getattr(pos, "reason", "")
                _is_mc = "micro" in _reason.lower()
                entry_px = getattr(pos, "entry_price_usd", 0)
                # Restore TP flags from the persisted Position. Without this,
                # a redeploy mid-position resets tp1_hit→False and re-fires
                # Dip TP1 every cycle, halving the position repeatedly.
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
                    strategy=getattr(pos, "strategy", "scanner"),
                    tp1_hit=bool(getattr(pos, "take_profit_1_hit", False)),
                    tp2_hit=bool(getattr(pos, "take_profit_2_hit", False)),
                    current_price=entry_px,
                    peak_price=entry_px,
                    min_price_usd=entry_px,
                    pyramid_signal_score=getattr(pos, "signal_score", 0),
                    hh_hl_confirmed=getattr(pos, "hh_hl_confirmed", False),
                    scalp_meta=getattr(pos, "scalp_meta", None),
                )

        # Update prices and evaluate each position
        for addr, state in list(self._states.items()):
            await self._update_price(addr, state)
            if addr in self._states:
                await self._evaluate_position(addr, state)

    async def _fetch_volume_snapshot(self, token_address: str):
        """
        Pull DexScreener volume data (h24, h1, m5) for the best Solana pair.
        Returns (v_h24, v_h1, v_m5) or None on failure. Used by the volume-
        death exit check so we don't rely on stale cached values.
        """
        try:
            url = f"{DEXSCREENER_TOKEN}{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            pairs = [
                p for p in (data.get("pairs") or [])
                if (p.get("chainId") or "").lower() == "solana"
            ]
            if not pairs:
                return None
            best = max(
                pairs,
                key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0),
            )
            vol = best.get("volume") or {}
            return (
                float(vol.get("h24") or 0),
                float(vol.get("h1") or 0),
                float(vol.get("m5") or 0),
            )
        except Exception:
            return None

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

                    # Transaction rate tracking — used for txn collapse exit signal
                    _txn_buys  = pair.get("txns", {}).get("h1", {}).get("buys",  0)
                    _txn_sells = pair.get("txns", {}).get("h1", {}).get("sells", 0)
                    _total_txns = _txn_buys + _txn_sells
                    state.current_txns_h1 = _total_txns
                    if state.entry_txns_h1 == 0 and _total_txns > 0:
                        state.entry_txns_h1 = _total_txns

                    # Live bs_h1 / bs_m5 — synced to Position so sell paths can
                    # capture order-flow state at exit time (catches "we sold
                    # while buyers were returning" pattern).  Cap at 999 to
                    # avoid +inf serialization.
                    _txn_b_m5 = pair.get("txns", {}).get("m5", {}).get("buys",  0)
                    _txn_s_m5 = pair.get("txns", {}).get("m5", {}).get("sells", 0)
                    def _bs_ratio(b, s):
                        if s > 0:
                            return min(b / s, 999.0)
                        if b > 0:
                            return 999.0
                        return 0.0
                    if token_address in self.open_positions_ref:
                        tp = self.open_positions_ref[token_address]
                        tp.current_bs_h1 = _bs_ratio(_txn_buys, _txn_sells)
                        tp.current_bs_m5 = _bs_ratio(_txn_b_m5, _txn_s_m5)

                    # Volume window for stall detection (uses REST data only)
                    last_check = self._last_volume_check.get(token_address)
                    if (not last_check or
                            (datetime.now(timezone.utc) - last_check).total_seconds()
                            >= self.stall_interval * 60):
                        state.add_volume_window(
                            volume_h1,
                            buys=_txn_buys,
                            sells=_txn_sells
                        )
                        self._last_volume_check[token_address] = datetime.now(timezone.utc)

        except Exception as e:
            logger.debug(f"[PositionManager/{self.chain_name}] Price REST: {e}")

    def _apply_price_update(self, token_address: str, state: PositionState,
                             price: float, volume_h1: float,
                             volume_m5: float, liquidity_usd: float):
        """Apply a price update to state and sync back to the open position object."""
        # Sanity gate: reject price if it's >+20% from current_price in a
        # single tick. Aligns with the realtime stop gate in
        # check_stop_loss_realtime so corrupted ticks can't slip through one
        # path while being rejected on the other. Falls back to peak_price
        # then entry_price when current_price is unset (first tick).
        ref_price = (
            state.current_price if state.current_price > 0
            else state.peak_price if state.peak_price > 0
            else state.entry_price
        )
        if ref_price > 0 and price > ref_price * 1.20:
            logger.warning(
                f"[PositionManager/{self.chain_name}] ⚠️  Price spike rejected: "
                f"{state.token_symbol} {ref_price:.8f} → {price:.8f} "
                f"({(price/ref_price - 1)*100:+.1f}% single tick) — "
                f"likely corrupted feed data, ignoring"
            )
            return
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
            tp.current_price_ts = time.time()
            # Sync min_price for max-drawdown calc at sell time.  Without this,
            # tp.min_price_usd stayed at entry_price and drawdown read 0.
            if state.min_price_usd > 0:
                tp.min_price_usd = state.min_price_usd
            if state.entry_price > 0:
                tp.pnl_usd = (
                    (price / state.entry_price - 1)
                    * getattr(tp, "amount_usd", state.position_size_usd)
                )
                # Track max favorable excursion during hold
                pnl_pct = (price / state.entry_price - 1) * 100
                prev_peak = getattr(tp, "peak_pnl_pct", 0.0) or 0.0
                if pnl_pct > prev_peak:
                    tp.peak_pnl_pct = pnl_pct
                    entry_mono = getattr(tp, "entry_time_monotonic", 0) or 0
                    tp.peak_pnl_at_secs = (
                        int(time.monotonic() - entry_mono) if entry_mono > 0 else 0
                    )
                # Hold-time pnl snapshots — capture once per threshold crossing
                # so we can validate stale-exit hypotheses on forward data.
                # Also fire a holder-snapshot task at each threshold to measure
                # mid-hold distribution velocity (entry -> 30m -> 60m -> exit).
                _age_s = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
                if tp.hold_pnl_snapshots is None:
                    tp.hold_pnl_snapshots = {}
                for _label, _thresh_s in (("30m", 1800), ("60m", 3600), ("90m", 5400), ("120m", 7200)):
                    if _age_s >= _thresh_s and _label not in tp.hold_pnl_snapshots:
                        tp.hold_pnl_snapshots[_label] = round(pnl_pct, 2)
                        logger.info(
                            f"[PositionManager/{self.chain_name}] ⏱ HOLD SNAPSHOT: "
                            f"{state.token_symbol} @ {_label} pnl={pnl_pct:+.1f}%"
                        )
                        # Fire holder + orderflow snapshots in the background —
                        # never block the price-tick path. Trader resolves the
                        # position by address and writes to the parallel
                        # snapshot dicts on Position. holder_snapshot also
                        # populates rugcheck_score_snapshots and lp_snapshots
                        # from the same rugcheck call (gaps 2 + 4).
                        try:
                            asyncio.create_task(
                                self.trader.capture_holder_snapshot(token_address, _label)
                            )
                            asyncio.create_task(
                                self.trader.capture_orderflow_snapshot(token_address, _label)
                            )
                        except Exception:
                            pass

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

            # ── MC HARD EXPIRY — 20-minute absolute cap before TP1 ───────
            # 73% of post-graduation tokens collapse below migration price
            # within 20 minutes (MemeTrans 2026). If TP1 hasn't fired in
            # 20 minutes the token isn't moving — exit at any P&L.
            if not state.tp1_hit and age_seconds >= 1200:
                logger.warning(
                    f"[PositionManager/{self.chain_name}] ⏱ MC 20MIN EXPIRY: "
                    f"{state.token_symbol} — no TP1 after 20min ({pnl_pct:+.1f}%)"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"MC 20min expiry — no momentum"
                )
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=1800
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
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=14400  # 4h — token already ran, don't re-enter
                    )
                return

            # ── MC STOP LOSS ──────────────────────────────────────────────
            # Skip if realtime feed already claimed this position — prevents
            # a duplicate polling-loop sell racing against the realtime ensure_future.
            if token_address in self._stop_triggered:
                return
            _mc_stop = 35.0 if state.strategy == "graduation" else self.mc_stop_loss_pct
            if pnl_pct <= -_mc_stop:
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
                    reason=f"MC stop loss -{_mc_stop:.0f}%"
                )
                self.stop_loss_hits += 1
                cooldown = 86400 if is_flash_crash else 14400
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=cooldown
                    )
                return

            # ── MC TXN COLLAPSE — exit if txns/hr fell to <10% of entry ─────
            # Leading indicator: txn rate dies before price confirms the dump.
            # Require ≥50 entry txns to avoid false signals on thin tokens.
            # Only fires pre-TP1 after a 5-minute stabilization window.
            if (not state.tp1_hit
                    and state.entry_txns_h1 >= 50
                    and state.current_txns_h1 > 0
                    and state.current_txns_h1 < state.entry_txns_h1 * 0.10
                    and age_seconds >= 300):
                logger.info(
                    f"[PositionManager/{self.chain_name}] 📉 MC TXN COLLAPSE: "
                    f"{state.token_symbol} — {state.current_txns_h1} txns/hr "
                    f"vs {state.entry_txns_h1} at entry "
                    f"({state.current_txns_h1 / state.entry_txns_h1 * 100:.0f}% of baseline)"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"MC txn collapse — {state.current_txns_h1}/hr vs {state.entry_txns_h1}/hr"
                )
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=3600
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
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=14400  # 4h — token already ran
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
        # DIP BUY POSITION MANAGEMENT
        # ═══════════════════════════════════════════════════════════════
        if state.strategy == "dip_buy":

            # ── VOLUME DEATH EXIT ────────────────────────────────────
            # Close losing positions whose liquidity has structurally died.
            # Guards: only fires when we're already down ≥3% AND 30min+ into
            # the hold — so active BULL-class chop can't trip it (winners and
            # early positions are protected by pnl_pct > -3 condition).
            age_s = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
            if age_s >= 1800 and pnl_pct <= -3.0:
                snapshot = await self._fetch_volume_snapshot(token_address)
                if snapshot is not None:
                    v_h24, v_h1, v_m5 = snapshot
                    decay_threshold = v_h24 / 48.0 if v_h24 > 0 else 0
                    if v_m5 == 0 and v_h1 < decay_threshold:
                        logger.warning(
                            f"[PositionManager/{self.chain_name}] 💀 VOLUME DEATH: "
                            f"{state.token_symbol} pnl={pnl_pct:.1f}% "
                            f"vol_m5=$0 vol_h1=${v_h1/1e3:.1f}k (<${decay_threshold/1e3:.1f}k) "
                            f"— closing"
                        )
                        await self._execute_sell(
                            token_address, state,
                            pct=1.0,
                            reason=(
                                f"Volume death exit (pnl={pnl_pct:.1f}%, "
                                f"vol_m5=0, vol_h1=${v_h1/1e3:.1f}k)"
                            ),
                        )
                        if self.scanner:
                            self.scanner.register_stop_loss(
                                token_address, state.token_symbol,
                                state.current_price, cooldown_seconds=7200
                            )
                        return

            # ── DIP STOP LOSS ─────────────────────────────────────────
            if pnl_pct <= -self.dip_stop_pct:
                logger.warning(
                    f"[PositionManager/{self.chain_name}] 🛑 DIP STOP: "
                    f"{state.token_symbol} at {pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"Dip stop -{self.dip_stop_pct:.0f}%"
                )
                self.stop_loss_hits += 1
                return

            # ── DIP PRE-TP1 TRAIL and POST-TP1 TRAIL — DROPPED 2026-05-01 ──
            # Asymmetric exit analysis (scripts/asymmetric_exit_analysis.py) on
            # 133 paired trades since the rewrite found:
            #   - 0 of 133 positions had peak ≥ 25% (no moonshots in this regime)
            #   - Pre-TP1 trail: peak band 1-5% had -201% capture, 5-8% had -53%
            #     (firing at the peak's collapse, sealing in losses)
            #   - Post-TP1 trail: avg 6.67pp give-back from peak; 33 canonical
            #     TP1+trail exits netted +$40.77 vs flat-at-TP1's +$51.94
            #   - Flat 100% TP at +12% simulates +$32.72 better than current
            # Both trails removed; TP fires once at +12% and sells 100%. If
            # regime data later shows runners exist in regime=up, can reinstate
            # selectively.

            # ── DIP TAKE PROFIT ───────────────────────────────────────
            if pnl_pct >= self.dip_tp2_pct and not state.tp2_hit:
                state.tp2_hit = True
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🎯 DIP TP2: "
                    f"{state.token_symbol} +{pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.dip_tp2_sell,
                    reason=f"Dip TP2 +{pnl_pct:.1f}%"
                )
                return

            if pnl_pct >= self.dip_tp1_pct and not state.tp1_hit:
                state.tp1_hit = True
                logger.info(
                    f"[PositionManager/{self.chain_name}] 🎯 DIP TP1: "
                    f"{state.token_symbol} +{pnl_pct:.1f}%"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=self.dip_tp1_sell,
                    reason=f"Dip TP1 +{pnl_pct:.1f}%"
                )
                return

            return  # End dip_buy path

        # ═══════════════════════════════════════════════════════════════
        # SCALP POSITION MANAGEMENT
        # ═══════════════════════════════════════════════════════════════
        if state.strategy == "scalp":
            await self._evaluate_scalp(token_address, state)
            return

        # ═══════════════════════════════════════════════════════════════
        # STANDARD POSITION MANAGEMENT
        # ═══════════════════════════════════════════════════════════════

        # ── WINNER PROTECTION — trail from peak once up ≥10% (pre or post TP1)
        # Fires pre-TP1 too: a token that peaks at +30% then crashes back deserves
        # an exit near the peak, not at the breakeven floor. Minimum 10% peak gain
        # avoids triggering on normal entry-level volatility.
        _MIN_PEAK_FOR_TRAIL = 10.0
        _peak_gain_pct_std = (state.peak_price - state.entry_price) / state.entry_price * 100 if state.entry_price > 0 else 0
        if (state.peak_price > 0
                and _peak_gain_pct_std >= _MIN_PEAK_FOR_TRAIL
                and state.current_price <= state.peak_price * (1 - self.winner_trail_pct / 100)):
            drop_from_peak = (state.peak_price - state.current_price) / state.peak_price * 100
            logger.info(
                f"[PositionManager/{self.chain_name}] 🔒 WINNER TRAIL: "
                f"{state.token_symbol} -{drop_from_peak:.1f}% from peak "
                f"(peaked at +{_peak_gain_pct_std:.1f}%)"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Winner trail -{drop_from_peak:.1f}% from peak"
            )
            if self.scanner:
                self.scanner.register_stop_loss(
                    token_address, state.token_symbol, state.current_price,
                    cooldown_seconds=14400  # 4h — token already ran, don't re-enter
                )
            return

        # ── BREAKEVEN LOCK — once up 8%, protect at +3% ─────────────────
        # Trigger raised from 2%→8%: a 2% move is noise on volatile tokens,
        # not a real gain worth protecting. Floor raised from 0%→+3%: ensures
        # the exit actually covers slippage and nets a small profit.
        # Briefly lowered to 5% (commit 7119e67) but reverted: with only 12
        # records of post-Batch-2 trajectory data we can't tell if drawdowns
        # happened before or after crossing +5%, so a 5% trigger may have
        # killed TP1-bound runners that briefly dipped past their +5% peak.
        # Re-evaluate after ~30 more trades have peak/drawdown data.
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

        # ── EARLY MOMENTUM FAILURE EXIT — graduated time/threshold tiers ────
        # Winners pop fast (1-4 min) or grind positively. Losers drop early and sit.
        # Tier 1 — 3 min: fast dumps (rugs, coordinated sells) drop hard immediately
        # Tier 2 — 15 min: medium dumps that haven't recovered (extended from 5min)
        # Pyramids get tighter exits (bought at the top, higher reversal risk)
        if state.strategy != "dip_buy":
            _is_pyramid = "[PYRAMID]" in state.token_symbol
            _early_exit_reason = None
            if not state.tp1_hit:
                if age_seconds >= 1800 and pnl_pct <= -5.0:
                    _early_exit_reason = f"Early exit {pnl_pct:.1f}% — no momentum at 30min"
                elif age_seconds >= 180 and pnl_pct <= -8.0:
                    _early_exit_reason = f"Early exit {pnl_pct:.1f}% — fast dump at 3min"
                # Pyramids: tighter — bought at the top, exit sooner if reversing
                elif _is_pyramid and age_seconds >= 420 and pnl_pct <= -3.0:
                    _early_exit_reason = f"Early exit {pnl_pct:.1f}% — pyramid no momentum at 7min"
                elif _is_pyramid and age_seconds >= 180 and pnl_pct <= -5.0:
                    _early_exit_reason = f"Early exit {pnl_pct:.1f}% — pyramid fast dump at 3min"

            if _early_exit_reason:
                logger.info(
                    f"[PositionManager/{self.chain_name}] ⏱ EARLY EXIT: "
                    f"{state.token_symbol} {pnl_pct:+.1f}% at {age_seconds/60:.1f}min — {_early_exit_reason}"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=_early_exit_reason,
                )
                if self.scanner:
                    self.scanner.register_stop_loss(
                        token_address, state.token_symbol, state.current_price,
                        cooldown_seconds=7200  # 2h cooldown — bad entry, not rug
                    )
                return

        # ── TXN RATE COLLAPSE — leading indicator: momentum dead before price ───
        # If h1 txn rate falls to <10% of entry baseline, the market has walked
        # away. Exit proactively rather than waiting for the price to confirm.
        # Require ≥50 entry txns (thin tokens have naturally low, noisy counts).
        # 5-minute stabilization window — h1 window rolls on fresh entries.
        if (not state.tp1_hit
                and state.entry_txns_h1 >= 50
                and state.current_txns_h1 > 0
                and state.current_txns_h1 < state.entry_txns_h1 * 0.10
                and age_seconds >= 300):
            logger.info(
                f"[PositionManager/{self.chain_name}] 📉 TXN COLLAPSE: "
                f"{state.token_symbol} — {state.current_txns_h1} txns/hr "
                f"vs {state.entry_txns_h1} at entry "
                f"({state.current_txns_h1 / state.entry_txns_h1 * 100:.0f}% of baseline)"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Txn collapse — {state.current_txns_h1}/hr vs {state.entry_txns_h1}/hr"
            )
            if self.scanner:
                self.scanner.register_stop_loss(
                    token_address, state.token_symbol, state.current_price,
                    cooldown_seconds=3600
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
            if self.scanner:
                self.scanner.register_stop_loss(
                    token_address, state.token_symbol, state.current_price,
                    cooldown_seconds=14400  # 4h — token already ran, don't re-enter
                )
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

            # Pyramids disabled — 18% win rate, structurally buys at the top.
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

        # ── AVERAGE DOWN — DISABLED ──────────────────────────────────────
        # Disabled: adds capital to losers right before early exit tiers cut them.
        # A position at -2.7% that gets averaged doubles exposure into the 5min/-5%
        # check. Works directly against the graduated exit strategy.
        # Re-enable via config flag if strategy changes.

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

        # Sanity gates: reject single-tick price moves >20% in either direction.
        # 2026-04-27 BULL incident showed feed glitches causing 37x phantom
        # upticks (peak_price gets inflated → all trail/TP math becomes wrong)
        # and matching downward correction ticks (-97% in one tick → spurious
        # stop fires).  Real 20%+ moves typically span multiple ticks; a
        # single 20%+ tick is almost always a feed glitch.
        last_rt = self._last_realtime_price.get(token_address, 0)
        if last_rt > 0:
            if price_usd < last_rt * 0.80:
                logger.warning(
                    f"[PositionManager/{self.chain_name}] ⚠️  Realtime tick rejected: "
                    f"{state.token_symbol} {last_rt:.8f} → {price_usd:.8f} "
                    f"({(price_usd / last_rt - 1) * 100:.0f}% in one tick) — likely "
                    f"corrupted feed, ignoring"
                )
                return
            if price_usd > last_rt * 1.20:
                logger.warning(
                    f"[PositionManager/{self.chain_name}] ⚠️  Realtime tick rejected: "
                    f"{state.token_symbol} {last_rt:.8f} → {price_usd:.8f} "
                    f"(+{(price_usd / last_rt - 1) * 100:.0f}% in one tick) — likely "
                    f"corrupted feed, ignoring"
                )
                return
        self._last_realtime_price[token_address] = price_usd

        pnl_pct = (price_usd / state.entry_price - 1) * 100

        # Flash-crash gate: tighter -12% stop for first 90s on MC positions.
        # Rugs dump -25%+ in seconds; legitimate MC winners never dip -12% before
        # recovering (MARVIN +34% in 69s, FATCAT +25% in 5m — both move straight up).
        # After 90s revert to normal -25% MC stop for volatility tolerance.
        if state.is_micro_cap and age_seconds < 90:
            stop_pct = 12.0
        elif state.strategy == "graduation":
            stop_pct = 35.0
        elif state.strategy == "dip_buy":
            stop_pct = self.dip_stop_pct
        elif state.strategy == "scalp":
            stop_pct = self.scalp_stop_pct
        else:
            stop_pct = self.mc_stop_loss_pct if state.is_micro_cap else self.stop_loss_pct

        # Track min/peak even if no stop fires — needed for accurate
        # max_drawdown_pct and peak_pnl_pct on every sell. Without this,
        # the realtime path bypasses _update_price's min/peak tracking and
        # stops look like they had 0% drawdown on the trade record (a
        # surprising number of historical stops show max_dd=0 because of
        # this — the stop fired between poll cycles).
        if state.min_price_usd <= 0 or price_usd < state.min_price_usd:
            state.min_price_usd = price_usd
        if price_usd > state.peak_price:
            state.peak_price = price_usd
        # Sync to the persisted Position so trader.sell sees current values.
        _tp = self.open_positions_ref.get(token_address)
        if _tp is not None:
            _tp.current_price_usd = price_usd
            if state.min_price_usd > 0:
                _tp.min_price_usd = state.min_price_usd
            # Mirror _update_price's peak_pnl_pct tracking for stops that
            # fire between poll cycles (briefly-green-then-dumped trades).
            _prev_peak = getattr(_tp, "peak_pnl_pct", 0.0) or 0.0
            if pnl_pct > _prev_peak:
                _tp.peak_pnl_pct = pnl_pct
                _entry_mono = getattr(_tp, "entry_time_monotonic", 0) or 0
                _tp.peak_pnl_at_secs = (
                    int(time.monotonic() - _entry_mono) if _entry_mono > 0 else 0
                )

        if pnl_pct <= -stop_pct:
            self._stop_triggered.add(token_address)
            state.current_price = price_usd
            label = (
                f"Grad stop loss -{stop_pct:.0f}% [realtime]"
                if state.strategy == "graduation" else
                f"Dip stop -{stop_pct:.0f}% [realtime]"
                if state.strategy == "dip_buy" else
                f"Scalp stop -{stop_pct:.1f}% [realtime]"
                if state.strategy == "scalp" else
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

            # Verify the trigger tick against a fresh Jupiter AMM quote before
            # executing. WS feeds occasionally emit junk ticks (one tick at
            # -16.6%, next back to -0%); without a confirmation step we sell
            # on noise. Require the fresh price to be at least 60% of the way
            # to the stop (e.g., -9% for a -15% stop) to confirm the breach.
            trigger_pnl_pct = pnl_pct
            trigger_stop_pct = stop_pct

            async def _do_realtime_stop(addr, st, lbl, trigger_price, cd_seconds):
                try:
                    fresh_price = await self._fetch_jupiter_price(addr)
                    if fresh_price > 0 and st.entry_price > 0:
                        fresh_pnl = (fresh_price / st.entry_price - 1) * 100
                        if fresh_pnl > -trigger_stop_pct * 0.6:
                            logger.warning(
                                f"[PositionManager/{self.chain_name}] ⚡ REALTIME STOP "
                                f"rejected for {st.token_symbol}: tick={trigger_pnl_pct:.1f}% "
                                f"but Jupiter spot={fresh_pnl:.1f}% — discarding as tick noise"
                            )
                            self._stop_triggered.discard(addr)
                            return
                    if self.scanner:
                        self.scanner.register_stop_loss(
                            addr, st.token_symbol, trigger_price,
                            cooldown_seconds=cd_seconds
                        )
                    await self._execute_sell(addr, st, pct=1.0, reason=lbl)
                    self.stop_loss_hits += 1
                    if st.strategy == "scalp" and self.scalp_queue:
                        pnl_usd = st.position_size_usd * st.pnl_pct / 100
                        self.scalp_queue.on_scalp_close(addr, "stop_loss", pnl_usd)
                except Exception as e:
                    logger.error(
                        f"[PositionManager/{self.chain_name}] ⚡ Realtime stop sell failed for "
                        f"{st.token_symbol}: {e} — clearing trigger for retry"
                    )
                    self._stop_triggered.discard(addr)

            asyncio.ensure_future(_do_realtime_stop(
                token_address, state, label, price_usd, cooldown
            ))

    async def _evaluate_scalp(self, token_address: str, state: PositionState):
        """
        Scalp branch (4-phase rewrite):
          - Hard stop at -scalp_stop_pct (6%) → full close
          - Time exit: after scalp_time_exit_candles (4) 5m candles from entry_close_time,
            if pnl < scalp_time_exit_min_pct (5%) → full close
          - Safety belt: scalp_max_hold_minutes (45) → full close
          - TP2: pnl ≥ scalp_tp2_pct (15%) AND tp1_hit → sell scalp_tp2_sell (35%)
          - TP1: pnl ≥ scalp_tp1_pct (10%) → sell scalp_tp1_sell (50%), set tp1_hit
          - Runner: after TP2, no further action (winner_trail handled externally)
        """
        if token_address in self._stop_triggered:
            return
        pnl_pct = state.pnl_pct
        meta = getattr(state, "scalp_meta", None) or {}

        # 1) Hard stop
        if pnl_pct <= -self.scalp_stop_pct:
            logger.warning(
                f"[PositionManager/{self.chain_name}] 🛑 SCALP HARD STOP: "
                f"{state.token_symbol} at {pnl_pct:.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Scalp hard stop -{self.scalp_stop_pct:.1f}%"
            )
            self.stop_loss_hits += 1
            if self.scalp_queue:
                pnl_usd = state.position_size_usd * pnl_pct / 100
                self.scalp_queue.on_scalp_close(token_address, "stop_loss", pnl_usd)
            return

        # 2) Time exit — N 5m candles since entry candle close without +X% move
        entry_close_time = meta.get("entry_close_time")
        if entry_close_time:
            now_ts = datetime.now(timezone.utc).timestamp()
            candles_elapsed = (now_ts - float(entry_close_time)) / 300.0
            if (candles_elapsed >= self.scalp_time_exit_candles
                    and pnl_pct < self.scalp_time_exit_min_pct):
                logger.info(
                    f"[PositionManager/{self.chain_name}] ⏱ SCALP TIME EXIT: "
                    f"{state.token_symbol} @ {pnl_pct:.1f}% after "
                    f"{candles_elapsed:.1f} candles (<{self.scalp_time_exit_min_pct:.0f}%)"
                )
                await self._execute_sell(
                    token_address, state,
                    pct=1.0,
                    reason=f"Scalp time exit {candles_elapsed:.1f}c @ {pnl_pct:.1f}%"
                )
                if self.scalp_queue:
                    pnl_usd = state.position_size_usd * pnl_pct / 100
                    self.scalp_queue.on_scalp_close(token_address, "scalp_time_exit", pnl_usd)
                return

        # 3) Safety belt — 45min absolute max
        hold_seconds = (datetime.now(timezone.utc) - state.entry_time).total_seconds()
        if hold_seconds >= self.scalp_max_hold_minutes * 60:
            logger.info(
                f"[PositionManager/{self.chain_name}] ⏱ SCALP MAX HOLD: "
                f"{state.token_symbol} after {hold_seconds/60:.0f}min"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Scalp max hold {hold_seconds/60:.0f}min"
            )
            if self.scalp_queue:
                pnl_usd = state.position_size_usd * pnl_pct / 100
                self.scalp_queue.on_scalp_close(token_address, "scalp_max_hold", pnl_usd)
            return

        # 4) TP2 — after TP1, at +15%, sell 35% of remaining. The remaining
        # position size is small after the TP2 cut; if it's the final exit
        # action for the trade we still need to release the capital slot so
        # ScalpCapitalManager._open doesn't accumulate stale entries on
        # winning streaks. The runner half (post-TP2) is handled by the
        # winner_trail in the dip path; for scalp we treat TP2 as full close.
        if state.tp1_hit and not state.tp2_hit and pnl_pct >= self.scalp_tp2_pct:
            state.tp2_hit = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 SCALP TP2: "
                f"{state.token_symbol} +{pnl_pct:.1f}% sell {self.scalp_tp2_sell*100:.0f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.scalp_tp2_sell,
                reason=f"Scalp TP2 +{pnl_pct:.1f}%"
            )
            if self.scalp_queue:
                pnl_usd = state.position_size_usd * pnl_pct / 100
                self.scalp_queue.on_scalp_close(token_address, "scalp_tp2", pnl_usd)
            return

        # 5) TP1 — at +10%, sell 50%. Partial close — do NOT release the
        # capital slot; runner half is still open and will exit via TP2,
        # stop, time, or max-hold.
        if not state.tp1_hit and pnl_pct >= self.scalp_tp1_pct:
            state.tp1_hit = True
            logger.info(
                f"[PositionManager/{self.chain_name}] 🎯 SCALP TP1: "
                f"{state.token_symbol} +{pnl_pct:.1f}% sell {self.scalp_tp1_sell*100:.0f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=self.scalp_tp1_sell,
                reason=f"Scalp TP1 +{pnl_pct:.1f}%"
            )
            return

        # 6) Runner — past TP2, external winner_trail handles exits

    async def _execute_sell(self, token_address: str,
                             state: PositionState,
                             pct: float, reason: str):
        """Execute a sell through the main trader."""
        try:
            # Mirror state TP flags to Position before the sell. trader.sell()
            # calls _save_open_positions() afterwards, so this is the persist
            # point. Without it, state.tp1_hit (memory-only) is lost on restart
            # and Dip TP1 re-fires, halving the position every redeploy.
            _pos = self.open_positions_ref.get(token_address)
            if _pos is not None:
                _pos.take_profit_1_hit = bool(state.tp1_hit)
                _pos.take_profit_2_hit = bool(state.tp2_hit)
            await self.trader.sell(
                token_address=token_address,
                token_symbol=state.token_symbol,
                reason=reason,
                pct=pct
            )
            # Sync state.position_size_usd to the post-sell remaining size for
            # partial sells. trader.sell() reduces position.amount_usd by
            # (1 - pct) but state.position_size_usd was previously stale, so
            # downstream pnl_usd calculations (e.g. scalp_capital.record_close)
            # would over-report by the un-sold proportion. Full closes (pct
            # >= 1.0) drop the state entirely below.
            if 0 < pct < 1.0:
                state.position_size_usd = state.position_size_usd * (1.0 - pct)
            if pct >= 1.0 and token_address in self._states:
                # Universal 60-min cross-strategy cooldown on ALL full closes
                # (TP, time exit, manual, etc) — diversifies rotation and prevents
                # back-to-back re-buys of the same token. max() in register_stop_loss
                # preserves any existing longer cooldown (24h flash crash, 2h realtime stop).
                if self.scanner:
                    try:
                        self.scanner.register_stop_loss(
                            token_address=token_address,
                            token_symbol=state.token_symbol,
                            exit_price=state.current_price,
                            cooldown_seconds=3600,
                        )
                    except Exception as e:
                        logger.warning(
                            f"[PositionManager/{self.chain_name}] "
                            f"Close-cooldown register failed for {state.token_symbol}: {e}"
                        )
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
        # Don't pyramid a pyramid — each [PYRAMID] position gets a fresh PositionState
        # with pyramided=False, which would trigger an infinite chain. Block it here.
        if "[PYRAMID]" in state.token_symbol:
            return
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
