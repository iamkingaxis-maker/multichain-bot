"""
Adaptive Signal Thresholds
Self-tunes the minimum scanner score required based on:
  - Recent bot win rate (lower win rate → raise threshold)
  - Market conditions (bull vs bear → adjust aggressiveness)
  - Chain-specific performance (different thresholds per chain)
  - Time of day / week patterns (memecoin activity cycles)
  - Consecutive losses (circuit breaker)

In a bull market: nearly everything pumps — lower threshold ok
In a bear market: only the strongest signals should trigger
In sideways: focus on highest-quality signals only

The threshold adjusts gradually (±2 per cycle) to avoid
overcorrecting on small sample sizes.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta
import math

logger = logging.getLogger(__name__)

# Threshold bounds — never go outside these
ABSOLUTE_MIN_THRESHOLD = 50
ABSOLUTE_MAX_THRESHOLD = 85

# How much to adjust per evaluation cycle
ADJUSTMENT_STEP = 2

# How many recent trades to evaluate
EVAL_WINDOW = 20


@dataclass
class ThresholdState:
    """Current threshold state for one chain."""
    chain_id: str
    current_threshold: int
    baseline_threshold: int

    # Performance tracking
    recent_trades: List[dict] = field(default_factory=list)
    consecutive_losses: int = 0
    consecutive_wins: int = 0

    # Market condition tracking
    market_phase: str = "NEUTRAL"  # BULL, BEAR, NEUTRAL, SIDEWAYS
    phase_confidence: float = 0.0  # 0-1

    # Adjustment history
    adjustments: List[dict] = field(default_factory=list)
    last_adjusted: Optional[datetime] = None

    @property
    def win_rate(self) -> float:
        recent = self.recent_trades[-EVAL_WINDOW:]
        if not recent:
            return 0.50
        return sum(1 for t in recent if t["pnl_pct"] > 0) / len(recent)

    @property
    def avg_pnl(self) -> float:
        recent = self.recent_trades[-EVAL_WINDOW:]
        if not recent:
            return 0.0
        return sum(t["pnl_pct"] for t in recent) / len(recent)

    @property
    def has_enough_data(self) -> bool:
        return len(self.recent_trades) >= 5


class AdaptiveThresholdManager:
    """
    Manages self-tuning signal thresholds across all chains.
    Evaluates performance every hour and adjusts thresholds accordingly.
    """

    def __init__(self,
                 baseline_threshold: int = 65,
                 evaluation_interval_minutes: int = 60,
                 target_win_rate: float = 0.55,
                 aggressive_threshold: int = 55,
                 conservative_threshold: int = 75):
        self.baseline = baseline_threshold
        self.eval_interval = evaluation_interval_minutes
        self.target_win_rate = target_win_rate
        self.aggressive = aggressive_threshold    # Bull market minimum
        self.conservative = conservative_threshold  # Bear market minimum

        # Per-chain state
        self.chains: Dict[str, ThresholdState] = {}
        self._last_evaluation: Dict[str, datetime] = {}
        self._total_adjustments = 0

    def register_chain(self, chain_id: str):
        """Register a chain for threshold management."""
        if chain_id not in self.chains:
            self.chains[chain_id] = ThresholdState(
                chain_id=chain_id,
                current_threshold=self.baseline,
                baseline_threshold=self.baseline
            )
            logger.info(
                f"[AdaptiveThreshold] Registered {chain_id} "
                f"— starting threshold: {self.baseline}"
            )

    def get_threshold(self, chain_id: str) -> int:
        """Get the current threshold for a chain."""
        state = self.chains.get(chain_id)
        if not state:
            return self.baseline
        return state.current_threshold

    def record_trade(self, chain_id: str, signal_score: int,
                      pnl_pct: float, pnl_usd: float,
                      exit_reason: str = ""):
        """Record a trade result for threshold adjustment."""
        state = self.chains.get(chain_id)
        if not state:
            return

        trade = {
            "score": signal_score,
            "pnl_pct": pnl_pct,
            "pnl_usd": pnl_usd,
            "exit_reason": exit_reason,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        state.recent_trades.append(trade)

        # Update streaks
        if pnl_pct > 0:
            state.consecutive_wins += 1
            state.consecutive_losses = 0
        else:
            state.consecutive_losses += 1
            state.consecutive_wins = 0

        # Keep window bounded
        if len(state.recent_trades) > 100:
            state.recent_trades = state.recent_trades[-100:]

        # Check if it's time to evaluate and adjust
        self._maybe_adjust(chain_id, state)

    def record_market_price(self, chain_id: str, price_change_24h: float):
        """
        Update market phase based on overall market direction.
        Feed this with BTC or SOL price change to detect market conditions.
        """
        state = self.chains.get(chain_id)
        if not state:
            return

        if price_change_24h > 10:
            state.market_phase = "BULL"
            state.phase_confidence = min(1.0, price_change_24h / 30)
        elif price_change_24h > 3:
            state.market_phase = "NEUTRAL"
            state.phase_confidence = 0.5
        elif price_change_24h < -10:
            state.market_phase = "BEAR"
            state.phase_confidence = min(1.0, abs(price_change_24h) / 30)
        elif price_change_24h < -3:
            state.market_phase = "SIDEWAYS"
            state.phase_confidence = 0.5
        else:
            state.market_phase = "NEUTRAL"
            state.phase_confidence = 0.3

    def _maybe_adjust(self, chain_id: str, state: ThresholdState):
        """Check if we should adjust the threshold now."""
        now = datetime.now(timezone.utc)
        last = self._last_evaluation.get(chain_id)

        # Don't adjust too frequently
        if last and (now - last).total_seconds() < self.eval_interval * 60:
            # But always respond to extreme consecutive losses
            if state.consecutive_losses >= 5:
                self._emergency_raise(chain_id, state)
            return

        self._last_evaluation[chain_id] = now

        if not state.has_enough_data:
            return

        old_threshold = state.current_threshold
        new_threshold = self._calculate_new_threshold(state)

        if new_threshold != old_threshold:
            state.current_threshold = new_threshold
            state.last_adjusted = now
            self._total_adjustments += 1

            direction = "↑" if new_threshold > old_threshold else "↓"
            logger.info(
                f"[AdaptiveThreshold] {chain_id} threshold: "
                f"{old_threshold} → {new_threshold} {direction} | "
                f"WR: {state.win_rate*100:.1f}% | "
                f"Market: {state.market_phase} | "
                f"AvgPnL: {state.avg_pnl:+.1f}%"
            )

            state.adjustments.append({
                "from": old_threshold,
                "to": new_threshold,
                "reason": self._get_adjustment_reason(state),
                "win_rate": round(state.win_rate, 3),
                "market_phase": state.market_phase,
                "timestamp": now.isoformat()
            })

    def _calculate_new_threshold(self, state: ThresholdState) -> int:
        """Calculate the optimal threshold based on all factors."""
        threshold = state.current_threshold

        # Factor 1: Win rate vs target
        wr_diff = state.win_rate - self.target_win_rate
        if wr_diff < -0.10:
            # Win rate well below target — raise threshold significantly
            threshold += ADJUSTMENT_STEP * 2
        elif wr_diff < -0.05:
            # Win rate slightly below target — raise threshold
            threshold += ADJUSTMENT_STEP
        elif wr_diff > 0.15:
            # Win rate well above target — can lower threshold slightly
            threshold -= ADJUSTMENT_STEP
        elif wr_diff > 0.10:
            threshold -= ADJUSTMENT_STEP // 2

        # Factor 2: Market phase adjustment
        if state.market_phase == "BULL":
            # In bull market, slightly more aggressive is ok
            threshold -= int(ADJUSTMENT_STEP * state.phase_confidence)
        elif state.market_phase == "BEAR":
            # In bear market, be more conservative
            threshold += int(ADJUSTMENT_STEP * state.phase_confidence)
        elif state.market_phase == "SIDEWAYS":
            # Sideways: focus on quality
            threshold += ADJUSTMENT_STEP // 2

        # Factor 3: Recent average PnL
        if state.avg_pnl < -5:
            threshold += ADJUSTMENT_STEP  # Losing → be more selective
        elif state.avg_pnl > 20:
            threshold -= ADJUSTMENT_STEP // 2  # Winning → slight relaxation

        # Factor 4: Consecutive losses circuit breaker
        if state.consecutive_losses >= 3:
            threshold += ADJUSTMENT_STEP

        # Apply hard bounds
        threshold = max(ABSOLUTE_MIN_THRESHOLD,
                        min(ABSOLUTE_MAX_THRESHOLD, threshold))
        # Never go below aggressive floor or above conservative ceiling
        threshold = max(self.aggressive, min(self.conservative, threshold))

        return threshold

    def _emergency_raise(self, chain_id: str, state: ThresholdState):
        """Immediately raise threshold on 5+ consecutive losses."""
        new_threshold = min(
            ABSOLUTE_MAX_THRESHOLD,
            state.current_threshold + ADJUSTMENT_STEP * 2
        )
        if new_threshold != state.current_threshold:
            logger.warning(
                f"[AdaptiveThreshold] 🚨 Emergency raise on {chain_id}: "
                f"{state.current_threshold} → {new_threshold} "
                f"({state.consecutive_losses} consecutive losses)"
            )
            state.current_threshold = new_threshold
            state.consecutive_losses = 0  # Reset after emergency raise

    def _get_adjustment_reason(self, state: ThresholdState) -> str:
        reasons = []
        if state.win_rate < self.target_win_rate - 0.05:
            reasons.append(f"low WR {state.win_rate*100:.1f}%")
        elif state.win_rate > self.target_win_rate + 0.10:
            reasons.append(f"high WR {state.win_rate*100:.1f}%")
        if state.market_phase != "NEUTRAL":
            reasons.append(f"{state.market_phase} market")
        if state.consecutive_losses >= 3:
            reasons.append(f"{state.consecutive_losses} losses")
        return ", ".join(reasons) if reasons else "routine adjustment"

    def get_all_thresholds(self) -> Dict[str, int]:
        """Return current thresholds for all chains."""
        return {
            chain_id: state.current_threshold
            for chain_id, state in self.chains.items()
        }

    def get_stats(self) -> dict:
        stats = {"total_adjustments": self._total_adjustments, "chains": {}}
        for chain_id, state in self.chains.items():
            stats["chains"][chain_id] = {
                "current_threshold": state.current_threshold,
                "baseline": state.baseline_threshold,
                "delta": state.current_threshold - state.baseline_threshold,
                "win_rate_pct": round(state.win_rate * 100, 1),
                "market_phase": state.market_phase,
                "consecutive_losses": state.consecutive_losses,
                "consecutive_wins": state.consecutive_wins,
                "trades_tracked": len(state.recent_trades)
            }
        return stats

    def print_status(self):
        """Print current threshold status to terminal."""
        print("\n" + "="*56)
        print("  📈 ADAPTIVE THRESHOLD STATUS")
        print("="*56)
        for chain_id, state in self.chains.items():
            delta = state.current_threshold - state.baseline_threshold
            delta_str = f"+{delta}" if delta > 0 else str(delta)
            phase_emoji = {
                "BULL": "🟢", "BEAR": "🔴",
                "SIDEWAYS": "🟡", "NEUTRAL": "⚪"
            }.get(state.market_phase, "⚪")
            print(
                f"  {phase_emoji} {chain_id.upper():8} | "
                f"Threshold: {state.current_threshold} ({delta_str}) | "
                f"WR: {state.win_rate*100:.1f}% | "
                f"Market: {state.market_phase}"
            )
        print("="*56)
