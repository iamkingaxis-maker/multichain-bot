"""
Position Manager
Encodes the trader's exact rules for managing open positions.

Rules (from trader experience):

TAKE PROFIT:
  TP1: +50%  → sell 50% of position (lock in fast)
  TP2: +100% → sell 75% of remaining (sell most quickly)
  TP3: +150% → sell 75% of remaining (rare but happens)
  Moon bag: whatever is left rides indefinitely

STALL DETECTION:
  - Check volume every 30 minutes
  - If volume drops below 20% of entry-hour volume for 2 consecutive windows
  - AND position has been open at least 1 hour
  - → Sell 75%, hold 25% moon bag

STOP LOSS:
  - Hard stop at -10% from entry — no exceptions
  - No conditional skipping — clean and simple

AVERAGE DOWN:
  - Only trigger if position is less than 15% down from entry
  - Only if current volume is still above 50% of entry volume (fundamentals hold)
  - Only once per position — never twice
  - Add 50% of original position size
  - If average down triggers and position still hits -10% → stop out normally

MARKET CONDITIONS:
  - Monitor BTC 24h price change every 15 minutes
  - If BTC down 5%+ → raise scanner threshold to 85 (from 65)
  - If multiple simultaneous portfolio drops detected → raise threshold to 80
  - Resume normal threshold when BTC stabilizes (< 3% down 24h)
  - A signal scoring 90+ always fires regardless of market conditions
"""

import asyncio
import logging
import aiohttp
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

    # TP tracking
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False

    # Stall tracking
    volume_windows: List[VolumeWindow] = field(default_factory=list)
    stall_exit_done: bool = False
    current_m5_volume: float = 0.0   # Latest m5 volume for dual check
    current_h1_volume: float = 0.0   # Latest h1 volume for dual check

    # Average down tracking
    averaged_down: bool = False
    avg_down_price: float = 0.0

    # Pyramid tracking
    pyramided: bool = False
    pyramid_signal_score: int = 0
    hh_hl_confirmed: bool = False

    # Current state
    current_price: float = 0.0
    current_volume_usd: float = 0.0
    peak_price: float = 0.0

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
        """
        Tiered threshold based on entry volume activity.
        High entry ($100k+/hr)  → 15% — tight, token was hot
        Medium entry ($20k-100k/hr) → 20% — standard
        Low entry ($5k-20k/hr)  → 30% — looser, token was quieter
        """
        if self.entry_volume_usd >= 100_000:
            return 0.15
        elif self.entry_volume_usd >= 20_000:
            return 0.20
        else:
            return 0.30

    @property
    def is_stalled(self) -> bool:
        """
        True when BOTH conditions are met simultaneously:
          1. m5 run-rate (m5 × 12 = hourly equivalent) is below threshold
          2. h1 volume is also below threshold
          3. Price is flat or down (no point exiting a rising token)
          4. Entry volume baseline is set (guard against false early triggers)
          5. Only 1 window needed — 30 min of combined low volume is enough
        """
        if self.entry_volume_usd <= 0:
            return False   # Baseline not set yet

        if not self.volume_windows:
            return False   # No windows recorded yet

        threshold = self.entry_volume_usd * self.stall_threshold

        # m5 run-rate: annualize to hourly equivalent
        m5_hourly_rate = self.current_m5_volume * 12

        # Condition 1: m5 run-rate below threshold
        m5_stalled = m5_hourly_rate < threshold

        # Condition 2: h1 also below threshold (both declining together)
        h1_stalled = self.current_h1_volume < threshold

        # Condition 3: Price flat or down — no exit if still making highs
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

    # Keep volume_declining as an alias for backward compatibility
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
        # Keep last 8 windows (4 hours)
        if len(self.volume_windows) > 8:
            self.volume_windows = self.volume_windows[-8:]


class MarketConditionMonitor:
    """
    Monitors BTC price and overall market conditions.
    Adjusts scanner thresholds based on market state.
    """

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
        """Check market conditions every 15 minutes."""
        logger.info("[MarketMonitor] Started — watching BTC 24h change")
        while True:
            try:
                await self._check_conditions()
            except Exception as e:
                logger.error(f"[MarketMonitor] Error: {e}")
            await asyncio.sleep(900)  # 15 minutes

    async def _check_conditions(self):
        btc_change = await self._fetch_btc_change()
        if btc_change is None:
            return

        self.btc_change_24h = btc_change
        self.last_checked = datetime.now(timezone.utc)
        was_restricted = self.market_restricted

        if btc_change <= -self.btc_drop_threshold:
            if not self.market_restricted:
                self.market_restricted = True
                self.restriction_reason = (
                    f"BTC down {abs(btc_change):.1f}% in 24h"
                )
                logger.warning(
                    f"[MarketMonitor] ⚠️ MARKET RESTRICTED — "
                    f"{self.restriction_reason} | "
                    f"Min score raised to {self.restricted_threshold}"
                )
                for cb in self._on_restrict_callbacks:
                    try:
                        await cb(self.restriction_reason) \
                            if asyncio.iscoroutinefunction(cb) \
                            else cb(self.restriction_reason)
                    except Exception:
                        pass
        elif btc_change > -3.0:
            if self.market_restricted:
                self.market_restricted = False
                self.restriction_reason = ""
                logger.info(
                    f"[MarketMonitor] ✅ Market conditions normalized — "
                    f"BTC {btc_change:+.1f}% | "
                    f"Threshold back to {self.normal_threshold}"
                )
                for cb in self._on_resume_callbacks:
                    try:
                        await cb() if asyncio.iscoroutinefunction(cb) \
                            else cb()
                    except Exception:
                        pass

        logger.debug(
            f"[MarketMonitor] BTC 24h: {btc_change:+.1f}% | "
            f"Restricted: {self.market_restricted}"
        )

    async def _fetch_btc_change(self) -> Optional[float]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    COINGECKO_BTC,
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return data.get("bitcoin", {}).get("usd_24h_change", None)
        except Exception as e:
            logger.debug(f"[MarketMonitor] BTC fetch error: {e}")
            return None

    def get_current_threshold(self, signal_score: int = 0) -> int:
        """
        Return the current minimum score threshold.
        A signal scoring 90+ always fires regardless of conditions.
        """
        if signal_score >= self.override_score:
            return self.override_score  # Always fires

        if self.market_restricted:
            return self.restricted_threshold

        return self.normal_threshold

    def should_trade(self, signal_score: int) -> bool:
        """True if this signal should fire given current conditions."""
        return signal_score >= self.get_current_threshold(signal_score)

    def get_stats(self) -> dict:
        return {
            "btc_24h_change": round(self.btc_change_24h, 2),
            "market_restricted": self.market_restricted,
            "restriction_reason": self.restriction_reason,
            "current_threshold": self.get_current_threshold(),
            "last_checked": self.last_checked.isoformat()
            if self.last_checked else None
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

                 # Take profit tiers
                 tp1_pct: float = 50.0,    # +50% → sell 50%
                 tp1_sell: float = 0.50,
                 tp2_pct: float = 100.0,   # +100% → sell 75% of remaining
                 tp2_sell: float = 0.75,
                 tp3_pct: float = 150.0,   # +150% → sell 75% of remaining
                 tp3_sell: float = 0.75,

                 # Stop loss
                 stop_loss_pct: float = 10.0,   # Hard stop, no exceptions

                 # Winner protection — trail from peak after TP1
                 winner_trail_pct: float = 10.0,  # Close 100% if drops 10% from peak post-TP1

                 # Stall detection
                 stall_check_interval_min: int = 30,
                 stall_volume_threshold: float = 0.20,
                 stall_min_hours: float = 1.0,
                 stall_sell_pct: float = 0.75,

                 # Average down
                 avg_down_max_loss_pct: float = 7.0,
                 avg_down_min_volume_pct: float = 0.50,
                 avg_down_size_pct: float = 0.50,

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

        # TP settings
        self.tp1_pct = tp1_pct
        self.tp1_sell = tp1_sell
        self.tp2_pct = tp2_pct
        self.tp2_sell = tp2_sell
        self.tp3_pct = tp3_pct
        self.tp3_sell = tp3_sell

        # SL
        self.stop_loss_pct = stop_loss_pct
        self.winner_trail_pct = winner_trail_pct

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

        # Stats
        self.tp1_hits = 0
        self.tp2_hits = 0
        self.tp3_hits = 0
        self.stop_loss_hits = 0
        self.stall_exits = 0
        self.avg_downs = 0

    async def run(self):
        """Main position management loop — checks every 30 seconds."""
        logger.info(
            f"[PositionManager/{self.chain_name}] Started\n"
            f"  TP1: +{self.tp1_pct}% → sell {self.tp1_sell*100:.0f}%\n"
            f"  TP2: +{self.tp2_pct}% → sell {self.tp2_sell*100:.0f}% of remaining\n"
            f"  TP3: +{self.tp3_pct}% → sell {self.tp3_sell*100:.0f}% of remaining\n"
            f"  Stop: -{self.stop_loss_pct}% hard\n"
            f"  Avg down: only if <{self.avg_down_max_loss}% loss + volume ok"
        )
        while True:
            try:
                await self._management_cycle()
            except Exception as e:
                logger.error(f"[PositionManager/{self.chain_name}] Error: {e}")
            await asyncio.sleep(30)

    async def _management_cycle(self):
        """One full management cycle across all open positions."""
        open_addrs = set(self.open_positions_ref.keys())

        # Remove closed positions
        for addr in list(self._states.keys()):
            if addr not in open_addrs:
                del self._states[addr]

        # Initialize new positions
        for addr in open_addrs:
            if addr not in self._states:
                pos = self.open_positions_ref[addr]
                self._states[addr] = PositionState(
                    token_address=addr,
                    token_symbol=getattr(pos, "token_symbol", "?"),
                    chain_id=self.chain_id,
                    entry_price=getattr(pos, "entry_price_usd", 0),
                    entry_volume_usd=0.0,
                    position_size_usd=getattr(pos, "entry_usd_value",
                                              getattr(pos, "amount_sol_spent", 0)),
                    original_size_usd=getattr(pos, "entry_usd_value",
                                              getattr(pos, "amount_sol_spent", 0)),
                    entry_time=getattr(pos, "entry_time",
                                       datetime.now(timezone.utc)),
                    current_price=getattr(pos, "entry_price_usd", 0),
                    peak_price=getattr(pos, "entry_price_usd", 0),
                    # Pyramid wiring — reads signal quality from position
                    pyramid_signal_score=getattr(pos, "signal_score", 0),
                    hh_hl_confirmed=getattr(pos, "hh_hl_confirmed", False)
                )

        # Update prices and evaluate each position
        for addr, state in list(self._states.items()):
            await self._update_price(addr, state)
            if addr in self._states:  # May have been removed by sell
                await self._evaluate_position(addr, state)

    async def _update_price(self, token_address: str, state: PositionState):
        """Fetch current price and volume from DexScreener."""
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
                    price = float(pair.get("priceUsd", 0) or 0)
                    volume_data = pair.get("volume", {})
                    volume_h1 = volume_data.get("h1", 0) or 0
                    volume_m5 = volume_data.get("m5", 0) or 0

                    if price > 0:
                        state.current_price = price
                        state.current_volume_usd = volume_h1
                        state.current_h1_volume = volume_h1
                        state.current_m5_volume = volume_m5
                        if price > state.peak_price:
                            state.peak_price = price

                        # Sync live price/PnL back to the trader Position object
                        # so the dashboard can display real-time unrealized P&L
                        if token_address in self.open_positions_ref:
                            tp = self.open_positions_ref[token_address]
                            tp.current_price_usd = price
                            if state.entry_price > 0:
                                tp.pnl_usd = (
                                    (price / state.entry_price - 1)
                                    * getattr(tp, "amount_usd", state.position_size_usd)
                                )

                        # Set entry volume baseline on first update (h1 snapshot)
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

                    # Add to volume window every 30 min
                    last_check = self._last_volume_check.get(token_address)
                    if (not last_check or
                            (datetime.now(timezone.utc) - last_check).total_seconds()
                            >= self.stall_interval * 60):
                        state.add_volume_window(
                            volume_h1,
                            buys=pair.get("txns", {}).get("h1", {}).get("buys", 0),
                            sells=pair.get("txns", {}).get("h1", {}).get("sells", 0)
                        )
                        self._last_volume_check[token_address] = \
                            datetime.now(timezone.utc)

        except Exception as e:
            logger.debug(f"[PositionManager/{self.chain_name}] Price: {e}")

    async def _evaluate_position(self, token_address: str,
                                  state: PositionState):
        """Check all exit and management rules for one position."""
        if state.current_price <= 0 or state.entry_price <= 0:
            return

        pnl_pct = state.pnl_pct

        # ── WINNER PROTECTION — close 100% if price drops 10% from peak after TP1 ──
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

        # ── STOP LOSS — Hard stop, no exceptions ─────────────────────────
        if pnl_pct <= -self.stop_loss_pct:
            logger.warning(
                f"[PositionManager/{self.chain_name}] 🛑 STOP LOSS: "
                f"{state.token_symbol} at {pnl_pct:.1f}%"
            )
            await self._execute_sell(
                token_address, state,
                pct=1.0,
                reason=f"Stop loss -{self.stop_loss_pct}%"
            )
            self.stop_loss_hits += 1
            # Block re-entry on this token for 4h
            if self.scanner:
                self.scanner.register_stop_loss(token_address, state.token_symbol, state.current_price)
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

            # Pyramid if original score was 90+
            if (not state.pyramided and
                    state.pyramid_signal_score >= 90 and
                    state.hh_hl_confirmed):
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
                f"✅ Sold 75% | 🎟 Kept 25% moon bag\n"
                f"⏱ Open: {state.hours_open:.1f}h"
            )
            return

        # ── AVERAGE DOWN ─────────────────────────────────────────────────
        # Require >= 5 minutes open — prevents triggering on the very first price
        # update where entry_volume and current_volume are the same snapshot.
        if (not state.averaged_down and
                state.hours_open >= (5 / 60) and
                -self.avg_down_max_loss <= pnl_pct < 0):
            # Check volume is still healthy
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
        except Exception as e:
            logger.error(
                f"[PositionManager/{self.chain_name}] Sell error: {e}"
            )

    async def _execute_pyramid(self, token_address: str,
                               state, pnl_pct: float):
        """Add 30% of original position after TP1 on 90+ score signals."""
        try:
            add_usd = state.position_size_usd * 0.30
            logger.info(
                f"[PositionManager/{self.chain_name}] PYRAMID: "
                f"{state.token_symbol} +{pnl_pct:.1f}% "
                f"score {state.pyramid_signal_score} adding ${add_usd:.0f}"
            )
            await self.trader.buy(
                token_address=token_address,
                token_symbol=f"{state.token_symbol}[PYRAMID]",
                reason=f"Pyramid TP1 score {state.pyramid_signal_score} HH+HL"
            )
            state.pyramided = True
            await self.telegram.send(
                f"PYRAMID [{self.chain_name}] "
                f"${state.token_symbol} +{pnl_pct:.1f}% "
                f"score {state.pyramid_signal_score} +${add_usd:.0f}"
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
            "peak_price": state.peak_price
        }
