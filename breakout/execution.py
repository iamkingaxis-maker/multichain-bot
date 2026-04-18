"""
BreakoutExecution — opens & manages breakout positions.

Task 11 covers `enter()` + cooldown helpers.
Task 12 adds `manage_positions()` and exit paths.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from breakout.state import BreakoutPosition
from breakout.scoring import (
    is_bearish_engulfing,
    has_upper_wick_rejection,
    volume_drop,
)

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BreakoutExecution:
    def __init__(self, *, data_client, paper_fill, capital, state, db, config):
        self.client = data_client
        self.paper_fill = paper_fill
        self.capital = capital
        self.state = state
        self.db = db
        self.config = config

    def can_open(self) -> bool:
        return self.capital.has_capacity(self.config.breakout_position_usd)

    def is_in_cooldown(self, symbol: str) -> bool:
        return self.db.is_in_cooldown(symbol, now_ts=_utcnow_iso())

    async def enter(self, *, symbol, candle, score, breakdown, resistance, reason) -> None:
        if symbol in self.state.open_positions:
            logger.info(f"[BreakoutExecution] duplicate {symbol} — no-op")
            return
        if not self.can_open():
            logger.info(f"[BreakoutExecution] no capacity — {symbol} skipped")
            return
        if self.is_in_cooldown(symbol):
            logger.info(f"[BreakoutExecution] in cooldown — {symbol} skipped")
            return

        position_usd = self.config.breakout_position_usd
        fill = await self.paper_fill.simulate_buy(symbol, usd_amount=position_usd)

        tp_price = fill.price * (1 + self.config.breakout_tp_pct / 100)
        stop_price = fill.price * (1 - self.config.breakout_stop_pct / 100)

        pos = BreakoutPosition(
            symbol=symbol,
            entry_time=_utcnow_iso(),
            entry_price=fill.price,
            qty=fill.qty,
            cost_usd=position_usd,
            score=score,
            resistance_level=resistance,
            tp_price=tp_price,
            stop_price=stop_price,
            entry_candle_volume=candle.volume,
            peak_price=fill.price,
            tp_hit=False,
            score_breakdown=dict(breakdown),
            reason_entry=reason,
        )

        self.capital.reserve(symbol, position_usd)
        self.state.open_positions[symbol] = pos

        self.db.insert_open_position(
            symbol=symbol,
            entry_time=pos.entry_time,
            entry_price=pos.entry_price,
            qty=pos.qty,
            cost_usd=pos.cost_usd,
            score=pos.score,
            score_breakdown=json.dumps(breakdown),
            resistance_level=pos.resistance_level,
            tp_price=pos.tp_price,
            stop_price=pos.stop_price,
            entry_candle_volume=pos.entry_candle_volume,
            peak_price=pos.peak_price,
        )

        logger.info(
            f"[BreakoutExecution] ENTRY {symbol} "
            f"price={fill.price:.6f} qty={fill.qty:.4f} "
            f"tp={tp_price:.6f} stop={stop_price:.6f} score={score}"
        )

    async def run(self):
        logger.info("[BreakoutExecution] Starting manage loop")
        while True:
            try:
                await self.manage_positions()
            except Exception as e:
                logger.error(f"[BreakoutExecution] Manage error: {e}")
            await asyncio.sleep(self.config.breakout_poll_interval_sec)

    async def manage_positions(self) -> None:
        for symbol, pos in list(self.state.open_positions.items()):
            try:
                klines = await self.client.fetch_klines(symbol, interval="15m", limit=3)
                if not klines:
                    continue
                current_price = klines[-1].close
                recent = klines[:-1] if len(klines) >= 2 else []
                await self._manage_one(pos, current_price=current_price, recent_k15=recent)
            except Exception as e:
                logger.debug(f"[BreakoutExecution] manage {symbol} error: {e}")

    async def _manage_one(self, pos: BreakoutPosition, *, current_price: float, recent_k15: list) -> None:
        symbol = pos.symbol
        pos.peak_price = max(pos.peak_price, current_price)

        if current_price <= pos.stop_price:
            await self._close(pos, qty_to_sell=pos.qty, reason="stop-loss")
            return

        entry_dt = datetime.fromisoformat(pos.entry_time)
        hold_hours = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
        if hold_hours >= self.config.breakout_max_hold_hours:
            await self._close(pos, qty_to_sell=pos.qty, reason="max-hold")
            return

        if len(recent_k15) >= 2:
            prev, curr = recent_k15[-2], recent_k15[-1]
            if current_price < pos.resistance_level:
                await self._close(pos, qty_to_sell=pos.qty, reason="breakout-failed")
                return
            if is_bearish_engulfing(prev, curr):
                await self._close(pos, qty_to_sell=pos.qty, reason="bearish-engulfing")
                return
            if has_upper_wick_rejection(curr):
                await self._close(pos, qty_to_sell=pos.qty, reason="wick-rejection")
                return
            if volume_drop(curr.volume, pos.entry_candle_volume):
                await self._close(pos, qty_to_sell=pos.qty, reason="volume-drop")
                return
        else:
            if current_price < pos.resistance_level:
                await self._close(pos, qty_to_sell=pos.qty, reason="breakout-failed")
                return

        if not pos.tp_hit and current_price >= pos.tp_price:
            half_qty = pos.qty * self.config.breakout_tp_sell_pct
            fill = await self.paper_fill.simulate_sell(symbol, qty=half_qty)
            pos.qty -= fill.qty
            pos.tp_hit = True
            if not hasattr(pos, "_partial_proceeds"):
                pos._partial_proceeds = 0.0
                pos._partial_fees = 0.0
            pos._partial_proceeds += fill.usd_proceeds
            pos._partial_fees += fill.fee_usd
            self.db.update_open_position(symbol, qty=pos.qty, tp_hit=1, peak_price=pos.peak_price)
            logger.info(f"[BreakoutExecution] TP1 {symbol} sold {fill.qty:.4f} @ {fill.price:.6f}")
            return

        if pos.tp_hit:
            trail_stop = pos.peak_price * (1 - self.config.breakout_trail_pct / 100)
            if current_price <= trail_stop:
                await self._close(pos, qty_to_sell=pos.qty, reason="trail")
                return

        self.db.update_open_position(symbol, peak_price=pos.peak_price)

    async def _close(self, pos: BreakoutPosition, *, qty_to_sell: float, reason: str) -> None:
        symbol = pos.symbol
        fill = await self.paper_fill.simulate_sell(symbol, qty=qty_to_sell)

        partial_proceeds = getattr(pos, "_partial_proceeds", 0.0)
        partial_fees = getattr(pos, "_partial_fees", 0.0)
        total_proceeds = partial_proceeds + fill.usd_proceeds
        total_fees = partial_fees + fill.fee_usd
        pnl_usd = total_proceeds - pos.cost_usd
        pnl_pct = (pnl_usd / pos.cost_usd) * 100 if pos.cost_usd > 0 else 0.0

        now_iso = _utcnow_iso()
        self.db.close_position(
            symbol=symbol,
            exit_time=now_iso,
            exit_price=fill.price,
            proceeds_usd=total_proceeds,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            reason_entry=pos.reason_entry,
            reason_exit=reason,
            fee_total_usd=total_fees,
        )

        if pnl_usd < 0:
            cooldown_until = (
                datetime.now(timezone.utc)
                + timedelta(minutes=self.config.breakout_cooldown_minutes)
            ).isoformat()
            self.db.set_cooldown(
                symbol=symbol,
                cooldown_until_ts=cooldown_until,
                last_loss_pnl_usd=pnl_usd,
                last_loss_time=now_iso,
            )

        self.capital.release(symbol, proceeds_usd=total_proceeds, cost_usd=pos.cost_usd)
        del self.state.open_positions[symbol]

        logger.info(
            f"[BreakoutExecution] EXIT {symbol} {reason} "
            f"pnl=${pnl_usd:+.2f} ({pnl_pct:+.2f}%) exit_price={fill.price:.6f}"
        )
