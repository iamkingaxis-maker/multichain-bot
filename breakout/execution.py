"""
BreakoutExecution — opens & manages breakout positions.

Task 11 covers `enter()` + cooldown helpers.
Task 12 adds `manage_positions()` and exit paths.
"""

import json
import logging
from datetime import datetime, timezone

from breakout.state import BreakoutPosition

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
