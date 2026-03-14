"""
Kelly Criterion Position Sizer
Dynamically sizes positions based on:
  - Historical win rate per signal score bucket
  - Average win/loss ratio
  - Current signal score (higher score = larger position)
  - Recent performance (drawdown protection)
  - Market conditions (volatility adjustment)

The Kelly formula: f* = (bp - q) / b
  Where:
    b = average win / average loss ratio
    p = probability of winning
    q = 1 - p (probability of losing)
    f* = fraction of capital to bet

We use fractional Kelly (50% of full Kelly) for safety.
Full Kelly is theoretically optimal but causes large drawdowns
in practice. Half Kelly gives ~75% of the return with much
lower variance.

Examples:
  Score 90, WR 65%, avg win 4x, avg loss 0.3x → ~8% position
  Score 70, WR 52%, avg win 2x, avg loss 0.3x → ~4% position
  Score 65, WR 48%, avg win 2x, avg loss 0.3x → ~2% position (minimum)
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


@dataclass
class ScoreBucket:
    """Performance statistics for a signal score range."""
    score_min: int
    score_max: int
    trades: int = 0
    wins: int = 0
    total_win_pct: float = 0.0
    total_loss_pct: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades > 0 else 0.50

    @property
    def loss_rate(self) -> float:
        return 1.0 - self.win_rate

    @property
    def avg_win_pct(self) -> float:
        wins = self.wins
        return self.total_win_pct / wins if wins > 0 else 1.0

    @property
    def avg_loss_pct(self) -> float:
        losses = self.trades - self.wins
        return self.total_loss_pct / losses if losses > 0 else 0.30

    @property
    def has_enough_data(self) -> bool:
        return self.trades >= 10

    def label(self) -> str:
        return f"{self.score_min}-{self.score_max}"


@dataclass
class VolatilityWindow:
    """Tracks recent market volatility for position adjustment."""
    returns: List[float] = field(default_factory=list)
    window_size: int = 20

    def add(self, return_pct: float):
        self.returns.append(return_pct)
        if len(self.returns) > self.window_size:
            self.returns = self.returns[-self.window_size:]

    @property
    def volatility(self) -> float:
        """Standard deviation of recent returns."""
        if len(self.returns) < 3:
            return 1.0
        n = len(self.returns)
        mean = sum(self.returns) / n
        variance = sum((r - mean) ** 2 for r in self.returns) / n
        return math.sqrt(variance)

    @property
    def volatility_multiplier(self) -> float:
        """
        1.0 = normal volatility
        < 1.0 = high volatility (reduce positions)
        > 1.0 = low volatility (can increase positions)
        """
        vol = self.volatility
        if vol <= 0:
            return 1.0
        # Normal vol assumed ~20%, scale inversely
        return min(1.5, max(0.5, 20.0 / (vol + 1)))


class KellySizer:
    """
    Dynamic position sizer using Kelly Criterion with safety caps.
    Works alongside the existing RiskManager.
    """

    def __init__(self,
                 total_capital: float,
                 kelly_fraction: float = 0.50,
                 min_position_pct: float = 0.02,
                 max_position_pct: float = 0.10,
                 default_position_pct: float = 0.05,
                 drawdown_reduction_threshold: float = 0.10):
        self.total_capital = total_capital
        self.kelly_fraction = kelly_fraction          # Use 50% of full Kelly
        self.min_pct = min_position_pct              # Never below 2%
        self.max_pct = max_position_pct              # Never above 10%
        self.default_pct = default_position_pct      # Default before data
        self.drawdown_threshold = drawdown_reduction_threshold

        # Score buckets: 50-59, 60-69, 70-79, 80-89, 90-100
        self.buckets: Dict[str, ScoreBucket] = {
            "50-59": ScoreBucket(50, 59),
            "60-69": ScoreBucket(60, 69),
            "70-79": ScoreBucket(70, 79),
            "80-89": ScoreBucket(80, 89),
            "90-100": ScoreBucket(90, 100),
        }

        self.volatility = VolatilityWindow()
        self.recent_pnl: List[float] = []
        self.peak_capital = total_capital
        self.current_capital = total_capital

        # Overall stats
        self.total_sized = 0
        self.kelly_used = 0      # Times Kelly was actually used vs default

    def get_position_size(self, signal_score: int,
                          available_capital: float) -> float:
        """
        Calculate optimal position size in USD for a given signal score.

        Returns USD amount to invest in this trade.
        """
        self.total_sized += 1
        bucket = self._get_bucket(signal_score)

        # Use default if not enough historical data
        if not bucket.has_enough_data:
            pct = self.default_pct
            logger.debug(
                f"[Kelly] Score {signal_score}: using default "
                f"{pct*100:.1f}% (insufficient data: {bucket.trades} trades)"
            )
        else:
            pct = self._calculate_kelly(bucket, signal_score)
            self.kelly_used += 1

        # Apply volatility adjustment
        vol_mult = self.volatility.volatility_multiplier
        pct *= vol_mult

        # Apply drawdown reduction
        drawdown_mult = self._drawdown_multiplier()
        pct *= drawdown_mult

        # Apply hard caps
        pct = max(self.min_pct, min(self.max_pct, pct))

        # Calculate USD amount
        usd_amount = available_capital * pct
        usd_amount = max(20.0, min(usd_amount, available_capital * 0.15))

        logger.info(
            f"[Kelly] Score {signal_score} | "
            f"Kelly%: {pct*100:.1f}% | "
            f"VolMult: {vol_mult:.2f} | "
            f"DDMult: {drawdown_mult:.2f} | "
            f"Size: ${usd_amount:.0f}"
        )

        return usd_amount

    def record_trade(self, signal_score: int, pnl_pct: float,
                     pnl_usd: float):
        """
        Record a completed trade to improve future sizing.
        Call this after every trade closes.
        """
        bucket = self._get_bucket(signal_score)
        bucket.trades += 1

        if pnl_pct > 0:
            bucket.wins += 1
            bucket.total_win_pct += pnl_pct
        else:
            bucket.total_loss_pct += abs(pnl_pct)

        # Update capital tracking
        self.current_capital += pnl_usd
        self.peak_capital = max(self.peak_capital, self.current_capital)

        # Update volatility tracker
        self.volatility.add(pnl_pct)
        self.recent_pnl.append(pnl_usd)
        if len(self.recent_pnl) > 50:
            self.recent_pnl = self.recent_pnl[-50:]

        logger.debug(
            f"[Kelly] Recorded score {signal_score}: "
            f"PnL {pnl_pct:+.1f}% | "
            f"Bucket {bucket.label()} WR: {bucket.win_rate*100:.1f}% "
            f"({bucket.trades} trades)"
        )

    def _calculate_kelly(self, bucket: ScoreBucket,
                          signal_score: int) -> float:
        """
        Apply the Kelly formula to determine position fraction.
        f* = (bp - q) / b
        """
        p = bucket.win_rate          # Probability of winning
        q = bucket.loss_rate         # Probability of losing
        b = bucket.avg_win_pct / bucket.avg_loss_pct  # Win/loss ratio

        if b <= 0 or p <= 0:
            return self.default_pct

        # Full Kelly fraction
        full_kelly = (b * p - q) / b

        if full_kelly <= 0:
            # Negative Kelly = edge is negative = don't bet
            logger.warning(
                f"[Kelly] Negative Kelly for bucket "
                f"{bucket.label()} — using minimum"
            )
            return self.min_pct

        # Apply fractional Kelly
        kelly_pct = full_kelly * self.kelly_fraction

        # Score bonus: higher score scores get up to 20% more
        score_normalized = (signal_score - 65) / 35  # 0 at 65, 1 at 100
        score_bonus = score_normalized * 0.20
        kelly_pct *= (1 + score_bonus)

        logger.debug(
            f"[Kelly] Bucket {bucket.label()} | "
            f"p={p:.2f} q={q:.2f} b={b:.2f} | "
            f"fullKelly={full_kelly:.3f} | "
            f"fractional={kelly_pct:.3f}"
        )

        return kelly_pct

    def _drawdown_multiplier(self) -> float:
        """
        Reduce position sizes during drawdown periods.
        At 10% drawdown: 75% of normal size
        At 20% drawdown: 50% of normal size
        At 30%+ drawdown: 25% of normal size
        """
        if self.peak_capital <= 0:
            return 1.0

        drawdown = (self.peak_capital - self.current_capital) / self.peak_capital

        if drawdown < self.drawdown_threshold:
            return 1.0
        elif drawdown < 0.20:
            return 0.75
        elif drawdown < 0.30:
            return 0.50
        else:
            return 0.25

    def _get_bucket(self, score: int) -> ScoreBucket:
        """Map a signal score to its performance bucket."""
        if score >= 90:
            return self.buckets["90-100"]
        elif score >= 80:
            return self.buckets["80-89"]
        elif score >= 70:
            return self.buckets["70-79"]
        elif score >= 60:
            return self.buckets["60-69"]
        else:
            return self.buckets["50-59"]

    def get_stats(self) -> dict:
        bucket_stats = {}
        for key, bucket in self.buckets.items():
            if bucket.trades > 0:
                bucket_stats[key] = {
                    "trades": bucket.trades,
                    "win_rate": round(bucket.win_rate * 100, 1),
                    "avg_win_pct": round(bucket.avg_win_pct, 1),
                    "avg_loss_pct": round(bucket.avg_loss_pct, 1),
                    "has_data": bucket.has_enough_data
                }

        return {
            "total_sized": self.total_sized,
            "kelly_used": self.kelly_used,
            "kelly_pct": round(self.kelly_used / self.total_sized * 100, 1)
            if self.total_sized > 0 else 0,
            "current_drawdown_pct": round(
                (self.peak_capital - self.current_capital) /
                self.peak_capital * 100, 1
            ) if self.peak_capital > 0 else 0,
            "volatility": round(self.volatility.volatility, 2),
            "vol_multiplier": round(self.volatility.volatility_multiplier, 2),
            "buckets": bucket_stats
        }

    def print_report(self):
        """Print a human-readable sizing report."""
        print("\n" + "="*56)
        print("  📐 KELLY SIZER REPORT")
        print("="*56)
        stats = self.get_stats()
        print(f"  Trades sized: {stats['total_sized']}")
        print(f"  Kelly used:   {stats['kelly_used']} ({stats['kelly_pct']}%)")
        print(f"  Drawdown:     {stats['current_drawdown_pct']}%")
        print(f"  Volatility:   {stats['volatility']}")
        print("-"*56)
        print("  Score Bucket Performance:")
        for bucket_key, bs in stats.get("buckets", {}).items():
            data_flag = "✅" if bs["has_data"] else "⏳"
            print(
                f"  {data_flag} [{bucket_key}] "
                f"WR: {bs['win_rate']}% | "
                f"AvgW: +{bs['avg_win_pct']}% | "
                f"AvgL: -{bs['avg_loss_pct']}% | "
                f"n={bs['trades']}"
            )
        print("="*56)
