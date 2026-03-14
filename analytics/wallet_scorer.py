"""
Wallet Auto-Scorer
Tracks the live performance of every copy wallet.
Automatically pauses wallets that underperform and
resumes them when they recover.

Metrics tracked per wallet:
  - Rolling win rate (last 20 trades)
  - Average return per trade
  - Max drawdown
  - Consecutive losses
  - Trade frequency (too fast = bot, skip)
  - Total PnL
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class WalletStatus(Enum):
    ACTIVE = "active"
    PAUSED = "paused"       # Temporarily paused due to poor performance
    BLOCKED = "blocked"     # Permanently blocked (suspected bot/manipulator)
    MONITORING = "monitoring"  # On probation — watching before full copy


@dataclass
class TradeRecord:
    """A single trade recorded for a wallet."""
    token_address: str
    token_symbol: str
    action: str              # "buy" or "sell"
    amount_usd: float
    pnl_usd: float           # Only populated on sells
    pnl_pct: float
    timestamp: datetime
    tx_hash: str = ""


@dataclass
class WalletScore:
    """Live scoring state for a single wallet."""
    address: str
    label: str               # Short display label
    chain_id: str
    status: WalletStatus = WalletStatus.MONITORING

    trades: List[TradeRecord] = field(default_factory=list)
    total_pnl_usd: float = 0.0
    paused_until: Optional[datetime] = None
    pause_reason: str = ""
    consecutive_losses: int = 0
    times_paused: int = 0
    added_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def completed_trades(self) -> List[TradeRecord]:
        return [t for t in self.trades if t.action == "sell"]

    @property
    def recent_trades(self) -> List[TradeRecord]:
        """Last 20 completed trades."""
        return self.completed_trades[-20:]

    @property
    def win_rate(self) -> float:
        recent = self.recent_trades
        if not recent:
            return 0.0
        wins = sum(1 for t in recent if t.pnl_pct > 0)
        return wins / len(recent) * 100

    @property
    def avg_return(self) -> float:
        recent = self.recent_trades
        if not recent:
            return 0.0
        return sum(t.pnl_pct for t in recent) / len(recent)

    @property
    def avg_win(self) -> float:
        wins = [t for t in self.recent_trades if t.pnl_pct > 0]
        return sum(t.pnl_pct for t in wins) / len(wins) if wins else 0

    @property
    def avg_loss(self) -> float:
        losses = [t for t in self.recent_trades if t.pnl_pct <= 0]
        return sum(t.pnl_pct for t in losses) / len(losses) if losses else 0

    @property
    def max_drawdown(self) -> float:
        """Worst consecutive loss streak in percentage terms."""
        if not self.recent_trades:
            return 0.0
        worst = 0.0
        running = 0.0
        for t in self.recent_trades:
            if t.pnl_pct < 0:
                running += t.pnl_pct
                worst = min(worst, running)
            else:
                running = 0.0
        return worst

    @property
    def profit_factor(self) -> float:
        """Gross profit / gross loss — above 1.5 is good."""
        wins = [t for t in self.recent_trades if t.pnl_pct > 0]
        losses = [t for t in self.recent_trades if t.pnl_pct <= 0]
        gross_profit = sum(t.pnl_pct for t in wins)
        gross_loss = abs(sum(t.pnl_pct for t in losses))
        return gross_profit / gross_loss if gross_loss > 0 else 999.0

    @property
    def overall_score(self) -> float:
        """Composite score 0-100."""
        if len(self.recent_trades) < 5:
            return 50.0  # Not enough data

        score = 0.0
        # Win rate (0-40 points)
        score += min(40, self.win_rate * 0.4)
        # Avg return (0-30 points)
        score += min(30, max(0, self.avg_return * 3))
        # Profit factor (0-20 points)
        score += min(20, self.profit_factor * 5)
        # Drawdown penalty (0 to -10 points)
        score += max(-10, self.max_drawdown * 0.5)

        return max(0, min(100, score))

    def summary(self) -> str:
        trades = len(self.recent_trades)
        return (
            f"Wallet {self.label} [{self.chain_id}] | "
            f"Status: {self.status.value} | "
            f"Score: {self.overall_score:.0f}/100 | "
            f"WR: {self.win_rate:.1f}% | "
            f"AvgR: {self.avg_return:+.1f}% | "
            f"PF: {self.profit_factor:.2f} | "
            f"Trades: {trades}"
        )


class WalletScorer:
    """
    Manages scoring and auto-pause logic for all copy wallets.
    The copy trader checks this before mirroring any trade.
    """

    def __init__(self,
                 min_win_rate: float = 50.0,
                 min_trades_before_scoring: int = 5,
                 max_consecutive_losses: int = 5,
                 pause_duration_minutes: int = 60,
                 auto_block_after_pauses: int = 3,
                 min_profit_factor: float = 1.2):
        self.min_win_rate = min_win_rate
        self.min_trades = min_trades_before_scoring
        self.max_consecutive_losses = max_consecutive_losses
        self.pause_duration = timedelta(minutes=pause_duration_minutes)
        self.auto_block_after_pauses = auto_block_after_pauses
        self.min_profit_factor = min_profit_factor

        self.wallets: Dict[str, WalletScore] = {}

    def register_wallet(self, address: str, chain_id: str) -> WalletScore:
        """Add a new wallet to track."""
        label = address[:6] + "..." + address[-4:]
        if address not in self.wallets:
            self.wallets[address] = WalletScore(
                address=address,
                label=label,
                chain_id=chain_id,
                status=WalletStatus.MONITORING
            )
            logger.info(f"[WalletScorer] Registered {label} on {chain_id}")
        return self.wallets[address]

    def is_copyable(self, address: str) -> bool:
        """
        Returns True if we should copy trades from this wallet right now.
        Called before every copy trade decision.
        """
        wallet = self.wallets.get(address)
        if not wallet:
            return True  # Unknown wallet — allow by default

        # Check if pause has expired
        if wallet.status == WalletStatus.PAUSED:
            if wallet.paused_until and datetime.now(timezone.utc) > wallet.paused_until:
                wallet.status = WalletStatus.MONITORING
                logger.info(
                    f"[WalletScorer] {wallet.label} resumed from pause"
                )
            else:
                return False

        if wallet.status == WalletStatus.BLOCKED:
            return False

        # Not enough data yet — allow copying but in monitoring mode
        if len(wallet.completed_trades) < self.min_trades:
            return True

        # Check performance thresholds
        if wallet.win_rate < self.min_win_rate:
            self._pause_wallet(wallet, f"Win rate {wallet.win_rate:.1f}% below {self.min_win_rate}%")
            return False

        if wallet.profit_factor < self.min_profit_factor:
            self._pause_wallet(wallet, f"Profit factor {wallet.profit_factor:.2f} below {self.min_profit_factor}")
            return False

        if wallet.consecutive_losses >= self.max_consecutive_losses:
            self._pause_wallet(wallet, f"{wallet.consecutive_losses} consecutive losses")
            return False

        return True

    def record_trade(self, address: str, token_address: str,
                     token_symbol: str, action: str,
                     amount_usd: float, pnl_usd: float = 0,
                     pnl_pct: float = 0, tx_hash: str = ""):
        """Record a trade for a wallet and re-evaluate its score."""
        wallet = self.wallets.get(address)
        if not wallet:
            return

        trade = TradeRecord(
            token_address=token_address,
            token_symbol=token_symbol,
            action=action,
            amount_usd=amount_usd,
            pnl_usd=pnl_usd,
            pnl_pct=pnl_pct,
            timestamp=datetime.now(timezone.utc),
            tx_hash=tx_hash
        )
        wallet.trades.append(trade)

        if action == "sell":
            wallet.total_pnl_usd += pnl_usd
            if pnl_pct > 0:
                wallet.consecutive_losses = 0
                if wallet.status == WalletStatus.MONITORING:
                    # Promote to active after enough wins
                    if (len(wallet.completed_trades) >= self.min_trades and
                            wallet.win_rate >= self.min_win_rate):
                        wallet.status = WalletStatus.ACTIVE
                        logger.info(
                            f"[WalletScorer] {wallet.label} promoted to ACTIVE "
                            f"(WR: {wallet.win_rate:.1f}%)"
                        )
            else:
                wallet.consecutive_losses += 1

            logger.info(f"[WalletScorer] {wallet.summary()}")

    def _pause_wallet(self, wallet: WalletScore, reason: str):
        """Pause a wallet temporarily."""
        wallet.status = WalletStatus.PAUSED
        wallet.paused_until = datetime.now(timezone.utc) + self.pause_duration
        wallet.pause_reason = reason
        wallet.times_paused += 1
        wallet.consecutive_losses = 0

        logger.warning(
            f"[WalletScorer] ⏸ PAUSED {wallet.label} — {reason} | "
            f"Times paused: {wallet.times_paused}"
        )

        # Block after too many pauses
        if wallet.times_paused >= self.auto_block_after_pauses:
            wallet.status = WalletStatus.BLOCKED
            logger.warning(
                f"[WalletScorer] 🚫 BLOCKED {wallet.label} — "
                f"paused {wallet.times_paused} times"
            )

    def get_leaderboard(self) -> List[WalletScore]:
        """Return wallets sorted by overall score."""
        return sorted(
            self.wallets.values(),
            key=lambda w: w.overall_score,
            reverse=True
        )

    def print_leaderboard(self):
        """Print wallet leaderboard to terminal."""
        board = self.get_leaderboard()
        print("\n" + "="*56)
        print("  👛 WALLET LEADERBOARD")
        print("="*56)
        for i, w in enumerate(board, 1):
            status_emoji = {
                WalletStatus.ACTIVE: "🟢",
                WalletStatus.MONITORING: "🟡",
                WalletStatus.PAUSED: "⏸",
                WalletStatus.BLOCKED: "🚫"
            }.get(w.status, "❓")
            print(
                f"  {i}. {status_emoji} {w.label} [{w.chain_id}] | "
                f"Score: {w.overall_score:.0f} | "
                f"WR: {w.win_rate:.1f}% | "
                f"PnL: ${w.total_pnl_usd:+.0f}"
            )
        print("="*56 + "\n")

    def get_stats(self) -> dict:
        active = sum(1 for w in self.wallets.values()
                     if w.status == WalletStatus.ACTIVE)
        paused = sum(1 for w in self.wallets.values()
                     if w.status == WalletStatus.PAUSED)
        blocked = sum(1 for w in self.wallets.values()
                      if w.status == WalletStatus.BLOCKED)
        return {
            "total_wallets": len(self.wallets),
            "active": active,
            "paused": paused,
            "blocked": blocked,
            "monitoring": len(self.wallets) - active - paused - blocked
        }
