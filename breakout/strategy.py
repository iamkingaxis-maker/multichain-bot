"""
BreakoutStrategy — entry engine.

Polls each watchlist coin every ~30s. Detects 15m candle-close transitions
(using close_time > last_seen_close). On transition: compute EMAs,
resistance, avg volume, run all gates, score, and call execution.enter().
"""

import asyncio
import logging

from breakout.scoring import (
    ema,
    breakout_strength_score,
)
from breakout.state import BreakoutState

logger = logging.getLogger(__name__)


class BreakoutStrategy:
    def __init__(self, data_client, state: BreakoutState, config, execution):
        self.client = data_client
        self.state = state
        self.config = config
        self.execution = execution

    async def run(self):
        logger.info("[BreakoutStrategy] Starting")
        while True:
            try:
                await self.poll_once()
            except Exception as e:
                logger.error(f"[BreakoutStrategy] Poll error: {e}")
            if self.state.scan_counters:
                logger.info(f"[BreakoutStrategy] scan counters: {self.state.scan_counters}")
                self.state.reset_scan_counters()
            await asyncio.sleep(self.config.breakout_poll_interval_sec)

    async def poll_once(self) -> None:
        for symbol in list(self.state.watchlist):
            try:
                await self._evaluate_symbol(symbol)
            except Exception as e:
                logger.debug(f"[BreakoutStrategy] {symbol} evaluate error: {e}")

    async def _evaluate_symbol(self, symbol: str) -> None:
        k15_latest = await self.client.fetch_klines(symbol, interval="15m", limit=2)
        if not k15_latest:
            return
        latest_close = k15_latest[-1].close_time
        last_seen = self.state.last_seen_close.get(symbol)
        if last_seen is not None and latest_close <= last_seen:
            return
        if self.config.breakout_candle_close_delay_sec > 0:
            await asyncio.sleep(self.config.breakout_candle_close_delay_sec)

        self.state.last_seen_close[symbol] = latest_close

        k15 = await self.client.fetch_klines(symbol, interval="15m", limit=25)
        k1h = await self.client.fetch_klines(symbol, interval="1h", limit=210)
        if len(k15) < 21 or len(k1h) < 50:
            return

        candle = k15[-1]
        prior = k15[-21:-1]
        resistance = max(k.high for k in prior)
        avg_volume_20 = sum(k.volume for k in prior) / 20

        closes_1h = [k.close for k in k1h]
        ema50_1h = ema(closes_1h, 50)
        ema200_1h = ema(closes_1h, 200)

        if not (candle.close > ema50_1h):
            self.state.bump("gate_price_below_ema50")
            return
        if not (ema50_1h > ema200_1h):
            self.state.bump("gate_ema50_below_ema200")
            return
        if not (candle.close > resistance):
            self.state.bump("gate_no_breakout")
            return
        if not (candle.volume > avg_volume_20):
            self.state.bump("gate_vol_below_avg")
            return

        consolidation_range = max(k.close for k in k15[-6:-1]) - min(k.close for k in k15[-6:-1])
        score, breakdown = breakout_strength_score(
            candle=candle,
            avg_volume_20=avg_volume_20,
            resistance=resistance,
            ema50_1h=ema50_1h,
            ema200_1h=ema200_1h,
            consolidation_range=consolidation_range,
        )

        if score < self.config.breakout_min_score:
            self.state.bump("gate_score_too_low")
            return
        if symbol in self.state.open_positions:
            self.state.bump("gate_duplicate")
            return
        if hasattr(self.execution, "can_open") and not self.execution.can_open():
            self.state.bump("gate_max_concurrent")
            return
        if hasattr(self.execution, "is_in_cooldown") and self.execution.is_in_cooldown(symbol):
            self.state.bump("gate_cooldown")
            return

        reason = (
            f"score={score} vol={breakdown['volume']} body={breakdown['body']} "
            f"break={breakdown['breakout_size']} trend={breakdown['trend']} "
            f"struct={breakdown['structure']} resistance={resistance:.6f}"
        )
        logger.info(f"[BreakoutStrategy] ENTRY {symbol} | {reason}")
        await self.execution.enter(
            symbol=symbol,
            candle=candle,
            score=score,
            breakdown=breakdown,
            resistance=resistance,
            reason=reason,
        )
