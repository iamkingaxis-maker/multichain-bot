"""
Position Scalper v2 — Redesigned
Trades WITHIN existing positions held by the Scanner and Copy Trader.
Requires zero separate capital — uses tokens the bot already holds.

How it works:
  1. Scanner/Copy Trader opens a position (e.g. 1000 PEPE at $0.10)
  2. Scalper watches price continuously
  3. When price rises significantly → partially sells from existing position
  4. Proceeds held as reserved cash
  5. If price dips back down by rebuy_trigger_pct → rebuys cheaper (more tokens)
  6. If price never dips in rebuy_window → cash pocketed as profit (Option C)
  7. Main bot still controls full position close via its own TP/SL
  8. Scalper stops when main bot closes the position

Key differences from v1:
  - No separate wallet or capital pool needed
  - No injecting new funds
  - Sells from existing position, rebuys with proceeds
  - Unredeployed cash → realized profit (Option C)
  - Main bot retains full control of position close timing
  - Sell trigger: +15% rise from reference price
  - Rebuy trigger: -20% drop from scalp sell price
  - Recovery confirmation required before rebuy
  - No forced hold time — rebuy window is 2 hours then Option C
"""

import asyncio
import logging
import aiohttp
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PriceObservation:
    price: float
    timestamp: datetime
    buy_count: int = 0
    sell_count: int = 0

    @property
    def buy_sell_ratio(self) -> float:
        total = self.buy_count + self.sell_count
        return self.buy_count / total if total > 0 else 0.5


@dataclass
class ScalpCycle:
    cycle_id: int
    token_address: str
    token_symbol: str
    sell_price: float
    tokens_sold: float
    cash_received_usd: float
    sell_time: datetime
    rebuy_price: float = 0.0
    tokens_rebuyed: float = 0.0
    rebuy_time: Optional[datetime] = None
    tokens_gained: float = 0.0
    closed: bool = False
    outcome: str = ""
    realized_profit_usd: float = 0.0

    def close_as_pocketed(self):
        self.closed = True
        self.outcome = "pocketed"
        self.realized_profit_usd = self.cash_received_usd

    def close_as_rebuyed(self, rebuy_price: float, tokens_rebuyed: float,
                          rebuy_time: datetime):
        self.rebuy_price = rebuy_price
        self.tokens_rebuyed = tokens_rebuyed
        self.rebuy_time = rebuy_time
        self.tokens_gained = tokens_rebuyed - self.tokens_sold
        self.closed = True
        self.outcome = "rebuyed"
        self.realized_profit_usd = self.tokens_gained * rebuy_price


@dataclass
class PositionScalpState:
    token_address: str
    token_symbol: str
    chain_id: str
    price_history: List[PriceObservation] = field(default_factory=list)
    completed_cycles: List[ScalpCycle] = field(default_factory=list)
    active_cycle: Optional[ScalpCycle] = None
    cycle_count: int = 0
    total_tokens_gained: float = 0.0
    total_profit_usd: float = 0.0
    peak_price: float = 0.0

    def add_price(self, obs: PriceObservation):
        self.price_history.append(obs)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
        self.price_history = [p for p in self.price_history if p.timestamp >= cutoff]
        if obs.price > self.peak_price:
            self.peak_price = obs.price

    @property
    def latest_price(self) -> float:
        return self.price_history[-1].price if self.price_history else 0.0

    @property
    def is_recovering(self) -> bool:
        if len(self.price_history) < 3:
            return False
        last = [p.price for p in self.price_history[-3:]]
        return last[-1] > last[-2] > last[0]

    @property
    def buy_pressure_returning(self) -> bool:
        if len(self.price_history) < 3:
            return False
        recent = self.price_history[-3:]
        avg = sum(p.buy_sell_ratio for p in recent) / len(recent)
        return avg > 0.55


class PositionScalper:
    """
    Scalper that trades within existing positions.
    No separate capital needed — sells from what the bot already holds.
    """

    def __init__(self, chain_name: str, chain_id: str, trader,
                 open_positions_ref: dict, telegram, tracker,
                 sell_trigger_pct: float = 15.0,
                 rebuy_trigger_pct: float = 20.0,
                 require_recovery_confirmation: bool = True,
                 scalp_sell_pct: float = 0.25,
                 max_cycles_per_position: int = 4,
                 rebuy_window_hours: float = 2.0,
                 min_profit_usd: float = 5.0):

        self.chain_name = chain_name
        self.chain_id = chain_id
        self.trader = trader
        self.open_positions_ref = open_positions_ref
        self.telegram = telegram
        self.tracker = tracker
        self.sell_trigger_pct = sell_trigger_pct
        self.rebuy_trigger_pct = rebuy_trigger_pct
        self.require_recovery = require_recovery_confirmation
        self.scalp_sell_pct = scalp_sell_pct
        self.max_cycles = max_cycles_per_position
        self.rebuy_window = timedelta(hours=rebuy_window_hours)
        self.min_profit_usd = min_profit_usd

        self._states: Dict[str, PositionScalpState] = {}
        self.total_cycles_completed = 0
        self.total_tokens_gained = 0.0
        self.total_profit_usd = 0.0
        self.cycles_rebuyed = 0
        self.cycles_pocketed = 0

    async def run(self):
        logger.info(
            f"[Scalper/{self.chain_name}] Started — "
            f"sell trigger: +{self.sell_trigger_pct}% | "
            f"rebuy trigger: -{self.rebuy_trigger_pct}% | "
            f"sell size: {self.scalp_sell_pct*100:.0f}% of position"
        )
        while True:
            try:
                await self._cycle()
            except Exception as e:
                logger.error(f"[Scalper/{self.chain_name}] Error: {e}")
            await asyncio.sleep(10)

    async def _cycle(self):
        open_addrs = set(self.open_positions_ref.keys())

        # Clean up closed positions
        for addr in list(self._states.keys()):
            if addr not in open_addrs:
                await self._handle_position_closed(addr)
                del self._states[addr]

        # Initialize new positions
        for addr in open_addrs:
            if addr not in self._states:
                pos = self.open_positions_ref[addr]
                self._states[addr] = PositionScalpState(
                    token_address=addr,
                    token_symbol=getattr(pos, "token_symbol", "?"),
                    chain_id=self.chain_id
                )

        # Update and evaluate
        for addr, state in self._states.items():
            await self._update_price(addr, state)
            await self._evaluate(addr, state)

    async def _update_price(self, token_address: str, state: PositionScalpState):
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status != 200:
                        return
                    data = await r.json()
                    pairs = [p for p in data.get("pairs", [])
                             if p.get("chainId") == self.chain_id]
                    if not pairs:
                        return
                    pair = max(pairs,
                               key=lambda p: p.get("liquidity", {}).get("usd", 0))
                    price = float(pair.get("priceUsd", 0) or 0)
                    if price <= 0:
                        return
                    txns = pair.get("txns", {}).get("m5", {})
                    state.add_price(PriceObservation(
                        price=price,
                        timestamp=datetime.now(timezone.utc),
                        buy_count=txns.get("buys", 0),
                        sell_count=txns.get("sells", 0)
                    ))
        except Exception as e:
            logger.debug(f"[Scalper/{self.chain_name}] Price fetch: {e}")

    async def _evaluate(self, token_address: str, state: PositionScalpState):
        if not state.price_history:
            return
        position = self.open_positions_ref.get(token_address)
        if not position:
            return

        # Manage active cycle
        if state.active_cycle and not state.active_cycle.closed:
            await self._check_expired(state)
            await self._check_rebuy(token_address, state, position)
            return

        # Check for new scalp sell
        if state.cycle_count < self.max_cycles:
            await self._check_scalp_sell(token_address, state, position)

    async def _check_scalp_sell(self, token_address: str,
                                  state: PositionScalpState, position):
        current = state.latest_price
        entry = getattr(position, "entry_price_usd", 0)
        if entry <= 0 or current <= 0:
            return

        # Reference price: last rebuy price or original entry
        if state.completed_cycles:
            last = state.completed_cycles[-1]
            ref = last.rebuy_price if last.outcome == "rebuyed" and last.rebuy_price > 0 else entry
        else:
            ref = entry

        rise_pct = ((current - ref) / ref) * 100
        if rise_pct < self.sell_trigger_pct:
            return

        tokens_held = getattr(position, "amount_tokens", 0)
        if tokens_held <= 0:
            return

        tokens_to_sell = tokens_held * self.scalp_sell_pct
        cash_expected = tokens_to_sell * current

        if cash_expected < self.min_profit_usd:
            return

        logger.info(
            f"[Scalper/{self.chain_name}] 📈 SCALP SELL: "
            f"{state.token_symbol} +{rise_pct:.1f}% | "
            f"Selling {self.scalp_sell_pct*100:.0f}% → ${cash_expected:.2f}"
        )

        sell_pct = min(tokens_to_sell / tokens_held, 0.50)
        success = await self._do_sell(token_address, state.token_symbol,
                                       sell_pct, position)
        if not success:
            return

        state.cycle_count += 1
        cycle = ScalpCycle(
            cycle_id=state.cycle_count,
            token_address=token_address,
            token_symbol=state.token_symbol,
            sell_price=current,
            tokens_sold=tokens_to_sell,
            cash_received_usd=cash_expected,
            sell_time=datetime.now(timezone.utc)
        )
        state.active_cycle = cycle

        await self.telegram.send(
            f"📈 *Scalp Sell* [{self.chain_name}]\n\n"
            f"🪙 ${state.token_symbol}\n"
            f"📊 Rise: +{rise_pct:.1f}% from reference\n"
            f"💰 Sold {self.scalp_sell_pct*100:.0f}% → ${cash_expected:.2f}\n"
            f"⏳ Watching for -{self.rebuy_trigger_pct}% dip to rebuy\n"
            f"🕐 Rebuy window: {int(self.rebuy_window.total_seconds()//3600)}h "
            f"then profit locked (Option C)"
        )

    async def _check_rebuy(self, token_address: str,
                            state: PositionScalpState, position):
        cycle = state.active_cycle
        current = state.latest_price

        dip_pct = ((cycle.sell_price - current) / cycle.sell_price) * 100
        if dip_pct < self.rebuy_trigger_pct:
            return

        # Recovery confirmation
        if self.require_recovery:
            if not state.is_recovering:
                logger.debug(
                    f"[Scalper/{self.chain_name}] {state.token_symbol} "
                    f"dipped {dip_pct:.1f}% but not recovering — waiting"
                )
                return
            if not state.buy_pressure_returning:
                logger.debug(
                    f"[Scalper/{self.chain_name}] {state.token_symbol} "
                    f"dipped {dip_pct:.1f}% — buy pressure not back yet"
                )
                return

        tokens_rebuyed = cycle.cash_received_usd / current
        tokens_gained = tokens_rebuyed - cycle.tokens_sold

        logger.info(
            f"[Scalper/{self.chain_name}] 📉 SCALP REBUY: "
            f"{state.token_symbol} -{dip_pct:.1f}% | "
            f"+{tokens_gained:.2f} tokens gained free"
        )

        success = await self._do_buy(token_address, state.token_symbol,
                                      cycle.cash_received_usd, position)
        if not success:
            return

        cycle.close_as_rebuyed(current, tokens_rebuyed,
                                datetime.now(timezone.utc))
        state.completed_cycles.append(cycle)
        state.active_cycle = None
        state.total_tokens_gained += tokens_gained
        state.total_profit_usd += cycle.realized_profit_usd
        self.total_cycles_completed += 1
        self.total_tokens_gained += tokens_gained
        self.total_profit_usd += cycle.realized_profit_usd
        self.cycles_rebuyed += 1

        await self.telegram.send(
            f"📉 *Scalp Rebuy* [{self.chain_name}] ✅\n\n"
            f"🪙 ${state.token_symbol}\n"
            f"📊 Dip: -{dip_pct:.1f}% from scalp sell\n"
            f"🎁 Gained: +{tokens_gained:.4f} tokens free\n"
            f"💰 Cycle profit: ${cycle.realized_profit_usd:.2f}\n"
            f"📈 Position total: +{state.total_tokens_gained:.4f} tokens | "
            f"${state.total_profit_usd:.2f}"
        )

    async def _check_expired(self, state: PositionScalpState):
        cycle = state.active_cycle
        if not cycle or cycle.closed:
            return
        age = datetime.now(timezone.utc) - cycle.sell_time
        if age < self.rebuy_window:
            return

        # Option C — pocket the cash
        cycle.close_as_pocketed()
        state.completed_cycles.append(cycle)
        state.active_cycle = None
        state.total_profit_usd += cycle.realized_profit_usd
        self.total_cycles_completed += 1
        self.total_profit_usd += cycle.realized_profit_usd
        self.cycles_pocketed += 1

        logger.info(
            f"[Scalper/{self.chain_name}] 💰 Option C — "
            f"{state.token_symbol}: ${cycle.cash_received_usd:.2f} pocketed"
        )
        await self.telegram.send(
            f"💰 *Scalp Profit Locked* [{self.chain_name}]\n\n"
            f"🪙 ${state.token_symbol}\n"
            f"📝 No -{self.rebuy_trigger_pct}% dip in "
            f"{int(self.rebuy_window.total_seconds()//3600)}h — pocketing\n"
            f"💵 Profit: ${cycle.cash_received_usd:.2f}\n"
            f"📈 Main position still open"
        )

    async def _handle_position_closed(self, token_address: str):
        state = self._states.get(token_address)
        if not state:
            return
        if state.active_cycle and not state.active_cycle.closed:
            state.active_cycle.close_as_pocketed()
            state.total_profit_usd += state.active_cycle.cash_received_usd
            self.cycles_pocketed += 1
        if state.completed_cycles:
            logger.info(
                f"[Scalper/{self.chain_name}] Position closed: "
                f"{state.token_symbol} | "
                f"Cycles: {len(state.completed_cycles)} | "
                f"+{state.total_tokens_gained:.4f} tokens | "
                f"${state.total_profit_usd:.2f} scalp profit"
            )

    async def _do_sell(self, token_address: str, token_symbol: str,
                        pct: float, position) -> bool:
        try:
            if hasattr(self.trader, "sell"):
                await self.trader.sell(
                    token_address=token_address,
                    token_symbol=f"{token_symbol}[SCALP]",
                    reason=f"Scalp partial sell {pct*100:.0f}%",
                    pct=pct
                )
                return True
        except Exception as e:
            logger.error(f"[Scalper/{self.chain_name}] Sell error: {e}")
        return False

    async def _do_buy(self, token_address: str, token_symbol: str,
                       usd_amount: float, position) -> bool:
        try:
            if hasattr(self.trader, "buy"):
                await self.trader.buy(
                    token_address=token_address,
                    token_symbol=f"{token_symbol}[SCALP-REBUY]",
                    reason=f"Scalp rebuy ${usd_amount:.2f}",
                    override_usd=usd_amount
                )
                return True
        except Exception as e:
            logger.error(f"[Scalper/{self.chain_name}] Buy error: {e}")
        return False

    def get_stats(self) -> dict:
        return {
            "chain": self.chain_name,
            "total_cycles": self.total_cycles_completed,
            "rebuyed": self.cycles_rebuyed,
            "pocketed": self.cycles_pocketed,
            "tokens_gained": round(self.total_tokens_gained, 6),
            "profit_usd": round(self.total_profit_usd, 2),
            "active_positions": len(self._states),
            "active_cycles": sum(
                1 for s in self._states.values()
                if s.active_cycle and not s.active_cycle.closed
            )
        }
