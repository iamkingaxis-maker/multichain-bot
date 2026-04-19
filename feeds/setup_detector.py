"""
SetupDetector — per-token state machine that walks:
  IDLE -> IMPULSE_FOUND -> PULLBACK_FOUND -> SWEEP_FOUND -> reclaim (fire)

A TriggerSignal emits when the most recent candle closes above the pullback
support AND all earlier phases validated AND R/R ≥ min_rr.
"""
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from feeds.candle_utils import Candle, rolling_avg_volume


class SetupPhase(str, Enum):
    IDLE = "idle"
    COOLDOWN = "cooldown"


@dataclass
class TriggerSignal:
    symbol: str
    entry_price: float
    stop_price: float
    tp1_price: float
    sweep_low: float
    reason: str


class SetupDetector:
    def __init__(self, symbol: str, cfg):
        self.symbol = symbol
        self.cfg = cfg
        self.phase: SetupPhase = SetupPhase.IDLE
        self._last_fired_close_time: int = 0
        self.last_reject: str = ""

    def _reject(self, reason: str) -> None:
        self.last_reject = reason
        return None

    def evaluate(self, candles: List[Candle]) -> Optional[TriggerSignal]:
        cfg = self.cfg
        # 5 = 3 pullback + 1 sweep + 1 reclaim; vol avg uses candles[:-2] tail
        need = max(cfg.scalp_sweep_vol_lookback + 5, cfg.scalp_impulse_lookback + 5, 25)
        if len(candles) < need:
            return self._reject("few_candles")

        reclaim = candles[-1]

        if reclaim.close_time == self._last_fired_close_time:
            return self._reject("cooldown")

        sweep = candles[-2]

        # Impulse: window ending just before the pullback leg
        impulse_end_idx = len(candles) - 5
        impulse_start_idx = max(0, impulse_end_idx - cfg.scalp_impulse_lookback)
        impulse_slice = candles[impulse_start_idx:impulse_end_idx]
        if not impulse_slice:
            return self._reject("impulse_empty")
        impulse_low = min(c.low for c in impulse_slice)
        impulse_high = max(c.high for c in impulse_slice)
        if impulse_low <= 0:
            return self._reject("impulse_zero")
        impulse_pct = (impulse_high - impulse_low) / impulse_low * 100
        if impulse_pct < cfg.scalp_impulse_min_pct:
            return self._reject(f"impulse_low({impulse_pct:.1f}%)")
        if impulse_pct > cfg.scalp_impulse_max_pct:
            return self._reject(f"impulse_high({impulse_pct:.1f}%)")

        # Pullback: 3 bars, retrace 30–60%
        pullback_slice = candles[impulse_end_idx:impulse_end_idx + 3]
        if len(pullback_slice) < 3:
            return self._reject("pullback_short")
        pullback_low = min(c.low for c in pullback_slice)
        retrace_pct = (impulse_high - pullback_low) / (impulse_high - impulse_low) * 100
        if retrace_pct < cfg.scalp_pullback_min_pct:
            return self._reject(f"retrace_low({retrace_pct:.0f}%)")
        if retrace_pct > cfg.scalp_pullback_max_pct:
            return self._reject(f"retrace_high({retrace_pct:.0f}%)")

        # Sweep: wicks below pullback low, long lower wick, vol ≥ 1.5× avg
        if sweep.low >= pullback_low:
            return self._reject("no_sweep")
        body = abs(sweep.close - sweep.open)
        lower_wick = min(sweep.open, sweep.close) - sweep.low
        if lower_wick <= max(body, 1e-12):
            return self._reject("no_wick")
        avg_vol = rolling_avg_volume(
            candles[:-2][-cfg.scalp_sweep_vol_lookback:],
            cfg.scalp_sweep_vol_lookback,
        )
        if avg_vol <= 0:
            return self._reject("no_avg_vol")
        if sweep.volume < avg_vol * cfg.scalp_sweep_vol_mult:
            return self._reject(f"sweep_vol({sweep.volume/avg_vol:.2f}x)")

        # Reclaim: close above pullback support
        if reclaim.close <= pullback_low:
            return self._reject("no_reclaim")

        entry = reclaim.close
        stop_from_sweep = sweep.low * 0.998
        stop_from_pct = entry * (1 - cfg.scalp_stop_pct / 100)
        # Prefer the shallower stop (sweep low if within 6% cap); 6% is the floor.
        stop = max(stop_from_sweep, stop_from_pct)
        tp1 = entry * (1 + cfg.scalp_tp1_pct / 100)
        if entry <= stop:
            return self._reject("entry_le_stop")
        rr = (tp1 - entry) / (entry - stop)
        if rr < cfg.scalp_min_rr:
            return self._reject(f"rr_low({rr:.2f})")

        self._last_fired_close_time = reclaim.close_time
        self.phase = SetupPhase.COOLDOWN
        self.last_reject = ""
        reason = (
            f"impulse={impulse_pct:.1f}% pullback={retrace_pct:.0f}% "
            f"sweep_vol={sweep.volume / avg_vol:.2f}x rr={rr:.2f}"
        )
        return TriggerSignal(
            symbol=self.symbol,
            entry_price=entry,
            stop_price=stop,
            tp1_price=tp1,
            sweep_low=sweep.low,
            reason=reason,
        )

    def reset(self):
        self.phase = SetupPhase.IDLE
        self._last_fired_close_time = 0
