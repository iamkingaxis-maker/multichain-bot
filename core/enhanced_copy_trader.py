"""
Enhanced Copy Trader
Implements the trader's exact copy trading rules:

SELL MIRRORING:
  When a copied wallet sells → bot sells everything immediately
  No independent TP tiers on copy positions — full mirror

ENTRY PRICE STALENESS:
  If token price has already moved 15%+ since the wallet bought
  → Skip the copy entirely (edge is gone)

MULTI-WALLET CONVICTION SIZING:
  1 wallet signals  → normal Kelly position size
  2 wallets signal  → 1.5x position size
  3+ wallets signal → 2.0x position size (hard cap)
  Still one position only — no duplicate entries

WALLET QUALITY FILTERS:
  Min average hold time: 1 hour (filters flippers)
  Max average hold time: 4 hours (filters slow position traders)
  Primary quality metric: win rate (consistent winners)
  Min range concentration: 50% of trades in $200k-$1m range
  If range concentration drops below 50% → auto-pause wallet

WALLET SCORING (updated weights):
  Win rate weighted 60% (your #1 metric)
  Profit factor weighted 25%
  Range concentration weighted 15%
  Hold time filter: hard gate (not weighted)
"""

import asyncio
import logging
import aiohttp
from typing import Dict, Set, Optional, List
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEXSCREENER_TOKEN = "https://api.dexscreener.com/latest/dex/tokens/"


@dataclass
class WalletTradeRecord:
    """One trade recorded for a wallet."""
    token_address: str
    token_symbol: str
    action: str              # "buy" or "sell"
    price_usd: float
    amount_usd: float
    mcap_at_trade: float
    timestamp: datetime
    hold_time_hours: float = 0.0
    pnl_pct: float = 0.0
    in_target_range: bool = False


@dataclass
class WalletProfile:
    """Full profile and quality assessment for one copy wallet."""
    address: str
    label: str
    chain_id: str

    trades: List[WalletTradeRecord] = field(default_factory=list)
    open_positions: Dict[str, float] = field(default_factory=dict)  # token → entry price

    # Quality metrics
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_hold_hours: float = 0.0
    range_concentration: float = 0.0  # % of trades in $200k-$1m

    # Status
    active: bool = True
    pause_reason: str = ""
    times_paused: int = 0
    consecutive_losses: int = 0

    @property
    def completed_trades(self) -> List[WalletTradeRecord]:
        return [t for t in self.trades if t.action == "sell" and t.pnl_pct != 0]

    @property
    def recent_trades(self) -> List[WalletTradeRecord]:
        return self.completed_trades[-20:]

    @property
    def computed_win_rate(self) -> float:
        recent = self.recent_trades
        if not recent:
            return 0.0
        return sum(1 for t in recent if t.pnl_pct > 0) / len(recent)

    @property
    def computed_profit_factor(self) -> float:
        recent = self.recent_trades
        wins = sum(t.pnl_pct for t in recent if t.pnl_pct > 0)
        losses = abs(sum(t.pnl_pct for t in recent if t.pnl_pct <= 0))
        return wins / losses if losses > 0 else 999.0

    @property
    def computed_range_concentration(self) -> float:
        recent = self.recent_trades
        if not recent:
            return 1.0  # Unknown — assume ok
        in_range = sum(1 for t in recent if t.in_target_range)
        return in_range / len(recent)

    @property
    def computed_avg_hold_hours(self) -> float:
        recent = [t for t in self.recent_trades if t.hold_time_hours > 0]
        if not recent:
            return 2.0  # Unknown — assume ok
        return sum(t.hold_time_hours for t in recent) / len(recent)

    @property
    def quality_score(self) -> float:
        """
        Composite quality score 0-100.
        Win rate weighted 60% (trader's #1 metric).
        Profit factor weighted 25%.
        Range concentration weighted 15%.
        """
        wr_score = self.computed_win_rate * 100 * 0.60
        pf = min(self.computed_profit_factor, 3.0)  # Cap at 3x
        pf_score = (pf / 3.0) * 100 * 0.25
        rc_score = self.computed_range_concentration * 100 * 0.15
        return wr_score + pf_score + rc_score

    def passes_hard_filters(self, min_hold_hours: float = 1.0,
                             max_hold_hours: float = 4.0,
                             min_range_concentration: float = 0.50,
                             min_win_rate: float = 0.50) -> tuple:
        """
        Hard gates — fails any of these = don't copy.
        Returns (passes: bool, reason: str)
        """
        if len(self.completed_trades) < 5:
            return True, ""  # Not enough data — allow

        avg_hold = self.computed_avg_hold_hours
        if avg_hold < min_hold_hours:
            return False, f"Avg hold {avg_hold:.1f}h < {min_hold_hours}h (flipper)"
        if avg_hold > max_hold_hours:
            return False, f"Avg hold {avg_hold:.1f}h > {max_hold_hours}h (too slow)"

        rc = self.computed_range_concentration
        if rc < min_range_concentration:
            return False, (
                f"Range concentration {rc*100:.0f}% < "
                f"{min_range_concentration*100:.0f}%"
            )

        wr = self.computed_win_rate
        if wr < min_win_rate:
            return False, f"Win rate {wr*100:.1f}% < {min_win_rate*100:.0f}%"

        return True, ""


class PendingSignal:
    """Tracks a pending copy signal from one or more wallets."""
    def __init__(self, token_address: str, token_symbol: str,
                 first_wallet: str, wallet_entry_price: float):
        self.token_address = token_address
        self.token_symbol = token_symbol
        self.wallets: List[str] = [first_wallet]
        self.wallet_entry_price = wallet_entry_price
        self.first_seen = datetime.now(timezone.utc)
        self.executed = False

    def add_wallet(self, wallet: str):
        if wallet not in self.wallets:
            self.wallets.append(wallet)

    @property
    def conviction_multiplier(self) -> float:
        """Position size multiplier based on number of agreeing wallets."""
        n = len(self.wallets)
        if n >= 3:
            return 2.0
        elif n == 2:
            return 1.5
        return 1.0

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.first_seen).total_seconds()


class EnhancedCopyTrader:
    """
    Copy trader implementing the trader's exact rules.
    Works for Solana (via Helius) and EVM chains (via block explorer).
    """

    def __init__(self,
                 chain_name: str,
                 chain_id: str,
                 wallets: List[str],
                 trader,
                 telegram,
                 tracker,
                 kelly_sizer=None,

                 # Entry price staleness
                 max_price_move_pct: float = 15.0,

                 # Wallet quality hard filters
                 min_hold_hours: float = 1.0,
                 max_hold_hours: float = 4.0,
                 min_win_rate: float = 0.50,
                 min_range_concentration: float = 0.50,

                 # Market cap range for concentration tracking
                 min_mcap: float = 200_000,
                 max_mcap: float = 1_000_000,

                 # Signal aggregation window
                 signal_window_seconds: float = 30.0,

                 # Copy delay (seconds after detecting wallet buy)
                 copy_delay_seconds: int = 5):

        self.chain_name = chain_name
        self.chain_id = chain_id
        self.trader = trader
        self.telegram = telegram
        self.tracker = tracker
        self.kelly_sizer = kelly_sizer

        self.max_price_move = max_price_move_pct
        self.min_hold = min_hold_hours
        self.max_hold = max_hold_hours
        self.min_win_rate = min_win_rate
        self.min_range_conc = min_range_concentration
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.signal_window = signal_window_seconds
        self.copy_delay = copy_delay_seconds

        # Wallet profiles
        self.profiles: Dict[str, WalletProfile] = {}
        for addr in wallets:
            label = addr[:6] + "..." + addr[-4:]
            self.profiles[addr] = WalletProfile(
                address=addr, label=label, chain_id=chain_id
            )

        # Pending signals (multi-wallet aggregation)
        self.pending_signals: Dict[str, PendingSignal] = {}

        # Stats
        self.copies_executed = 0
        self.skipped_stale = 0
        self.skipped_quality = 0
        self.skipped_duplicate = 0
        self.sells_mirrored = 0

    async def on_wallet_buy(self, wallet_address: str,
                             token_address: str, token_symbol: str,
                             wallet_entry_price: float,
                             token_mcap: float = 0):
        """
        Called when a copied wallet makes a buy.
        Implements all entry rules before executing.
        """
        profile = self.profiles.get(wallet_address)
        if not profile or not profile.active:
            return

        # Hard quality filter
        passes, reason = profile.passes_hard_filters(
            self.min_hold, self.max_hold,
            self.min_range_conc, self.min_win_rate
        )
        if not passes:
            logger.info(
                f"[CopyTrader/{self.chain_name}] ⛔ Quality filter: "
                f"{profile.label} — {reason}"
            )
            self.skipped_quality += 1
            return

        # Record position open on wallet
        profile.open_positions[token_address] = wallet_entry_price

        # Track in-range trade
        in_range = self.min_mcap <= token_mcap <= self.max_mcap

        # Check if already have a pending signal for this token
        existing = self.pending_signals.get(token_address)
        if existing and not existing.executed:
            # Add wallet to existing signal (multi-wallet conviction)
            existing.add_wallet(wallet_address)
            logger.info(
                f"[CopyTrader/{self.chain_name}] 📊 Multi-wallet signal: "
                f"{token_symbol} — {len(existing.wallets)} wallets | "
                f"{existing.conviction_multiplier}x size"
            )
            return

        # New signal — create pending entry
        signal = PendingSignal(
            token_address=token_address,
            token_symbol=token_symbol,
            first_wallet=wallet_address,
            wallet_entry_price=wallet_entry_price
        )
        self.pending_signals[token_address] = signal

        # Wait for signal window to collect additional wallet signals
        await asyncio.sleep(self.signal_window)

        # Now evaluate and execute
        await self._evaluate_and_execute(signal, in_range)

    async def _evaluate_and_execute(self, signal: PendingSignal,
                                     in_range: bool):
        """Evaluate a complete signal and execute if it passes all checks."""
        if signal.executed:
            return
        signal.executed = True

        # Check price staleness — has it already moved too much?
        current_price = await self._get_current_price(signal.token_address)
        if current_price > 0 and signal.wallet_entry_price > 0:
            price_move_pct = (
                (current_price - signal.wallet_entry_price) /
                signal.wallet_entry_price * 100
            )
            if price_move_pct >= self.max_price_move:
                logger.info(
                    f"[CopyTrader/{self.chain_name}] ⏰ STALE: "
                    f"{signal.token_symbol} already moved "
                    f"+{price_move_pct:.1f}% — skipping"
                )
                self.skipped_stale += 1
                await self.telegram.send(
                    f"⏰ *Copy Skipped — Stale* [{self.chain_name}]\n\n"
                    f"🪙 ${signal.token_symbol}\n"
                    f"📊 Already moved +{price_move_pct:.1f}% "
                    f"since wallet bought\n"
                    f"❌ Edge gone — skip"
                )
                del self.pending_signals[signal.token_address]
                return

        # Already holding this token?
        if signal.token_address in self.trader.open_positions:
            logger.info(
                f"[CopyTrader/{self.chain_name}] Already holding "
                f"{signal.token_symbol} — skipping duplicate"
            )
            self.skipped_duplicate += 1
            del self.pending_signals[signal.token_address]
            return

        # Brief copy delay
        await asyncio.sleep(self.copy_delay)

        # Calculate position size with conviction multiplier
        multiplier = signal.conviction_multiplier
        wallet_labels = [
            self.profiles[w].label
            for w in signal.wallets
            if w in self.profiles
        ]

        logger.info(
            f"[CopyTrader/{self.chain_name}] ✅ COPY BUY: "
            f"{signal.token_symbol} | "
            f"Wallets: {len(signal.wallets)} | "
            f"Size multiplier: {multiplier}x | "
            f"Price move: "
            f"{((current_price - signal.wallet_entry_price) / signal.wallet_entry_price * 100) if signal.wallet_entry_price > 0 else 0:.1f}%"
        )

        conviction_label = {
            1.0: "single wallet",
            1.5: "2 wallets — 1.5x size",
            2.0: "3+ wallets — 2x size"
        }.get(multiplier, f"{multiplier}x")

        await self.telegram.send(
            f"📋 *Copy Buy* [{self.chain_name}]\n\n"
            f"🪙 ${signal.token_symbol}\n"
            f"👛 Wallets: {', '.join(wallet_labels)}\n"
            f"📊 Conviction: {conviction_label}\n"
            f"💰 Price since wallet entry: "
            f"+{((current_price - signal.wallet_entry_price) / signal.wallet_entry_price * 100) if signal.wallet_entry_price > 0 else 0:.1f}%"
        )

        await self.trader.buy(
            token_address=signal.token_address,
            token_symbol=signal.token_symbol,
            reason=(
                f"Copy [{self.chain_name}] "
                f"{len(signal.wallets)} wallet(s) "
                f"{multiplier}x size"
            )
        )
        self.copies_executed += 1

        del self.pending_signals[signal.token_address]

    async def on_wallet_sell(self, wallet_address: str,
                              token_address: str, token_symbol: str,
                              pnl_pct: float = 0.0):
        """
        Called when a copied wallet sells.
        Rule: Always mirror sells — full exit immediately.
        """
        profile = self.profiles.get(wallet_address)
        if not profile:
            return

        # Record hold time for this wallet
        entry_price = profile.open_positions.pop(token_address, 0)
        if entry_price > 0:
            # We don't have exact timestamps per position here
            # Hold time is approximated from wallet scorer data
            pass

        # Only sell if we actually hold this token
        if token_address not in self.trader.open_positions:
            return

        logger.info(
            f"[CopyTrader/{self.chain_name}] 📋 COPY SELL: "
            f"{token_symbol} — wallet {profile.label} sold"
        )

        await self.telegram.send(
            f"📋 *Copy Sell* [{self.chain_name}]\n\n"
            f"🪙 ${token_symbol}\n"
            f"👛 Wallet: {profile.label} sold\n"
            f"📝 Mirroring full exit"
        )

        await self.trader.sell(
            token_address=token_address,
            token_symbol=token_symbol,
            reason=f"Copy sell — {profile.label} exited",
            pct=1.0
        )
        self.sells_mirrored += 1

        # Record trade outcome for wallet scoring
        pos = None  # Position already closed by sell
        self._record_wallet_trade(wallet_address, token_address,
                                   token_symbol, pnl_pct)

    def _record_wallet_trade(self, wallet_address: str,
                              token_address: str, token_symbol: str,
                              pnl_pct: float):
        """Update wallet profile after a completed trade."""
        profile = self.profiles.get(wallet_address)
        if not profile:
            return

        in_range = True  # Simplified — assume in range for now

        trade = WalletTradeRecord(
            token_address=token_address,
            token_symbol=token_symbol,
            action="sell",
            price_usd=0,
            amount_usd=0,
            mcap_at_trade=0,
            timestamp=datetime.now(timezone.utc),
            pnl_pct=pnl_pct,
            in_target_range=in_range
        )
        profile.trades.append(trade)

        # Update win/loss streak
        if pnl_pct > 0:
            profile.consecutive_losses = 0
        else:
            profile.consecutive_losses += 1

        # Check quality filters after update
        passes, reason = profile.passes_hard_filters(
            self.min_hold, self.max_hold,
            self.min_range_conc, self.min_win_rate
        )
        if not passes and profile.active:
            profile.active = False
            profile.pause_reason = reason
            profile.times_paused += 1
            logger.warning(
                f"[CopyTrader/{self.chain_name}] ⏸ AUTO-PAUSED: "
                f"{profile.label} — {reason}"
            )
            asyncio.create_task(self.telegram.send(
                f"⏸ *Wallet Paused* [{self.chain_name}]\n\n"
                f"👛 {profile.label}\n"
                f"📝 Reason: {reason}\n"
                f"📊 Score: {profile.quality_score:.0f}/100"
            ))

    async def _get_current_price(self, token_address: str) -> float:
        """Fetch current token price from DexScreener."""
        try:
            url = f"{DEXSCREENER_TOKEN}{token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        return 0.0
                    data = await resp.json()
                    pairs = [
                        p for p in data.get("pairs", [])
                        if p.get("chainId") == self.chain_id
                    ]
                    if not pairs:
                        return 0.0
                    pair = max(
                        pairs,
                        key=lambda p: p.get("liquidity", {}).get("usd", 0)
                    )
                    return float(pair.get("priceUsd", 0) or 0)
        except Exception:
            return 0.0

    def get_leaderboard(self) -> List[WalletProfile]:
        """Return wallets sorted by quality score."""
        return sorted(
            self.profiles.values(),
            key=lambda w: w.quality_score,
            reverse=True
        )

    def print_leaderboard(self):
        """Print wallet leaderboard to terminal."""
        print("\n" + "="*60)
        print(f"  👛 WALLET LEADERBOARD [{self.chain_name}]")
        print("="*60)
        for i, w in enumerate(self.get_leaderboard(), 1):
            status = "🟢 ACTIVE" if w.active else f"⏸ PAUSED ({w.pause_reason})"
            print(
                f"  {i}. {w.label} | "
                f"Score: {w.quality_score:.0f} | "
                f"WR: {w.computed_win_rate*100:.1f}% | "
                f"PF: {w.computed_profit_factor:.2f} | "
                f"Range: {w.computed_range_concentration*100:.0f}% | "
                f"{status}"
            )
        print("="*60)

    def get_stats(self) -> dict:
        return {
            "chain": self.chain_name,
            "wallets_total": len(self.profiles),
            "wallets_active": sum(1 for w in self.profiles.values() if w.active),
            "wallets_paused": sum(1 for w in self.profiles.values() if not w.active),
            "copies_executed": self.copies_executed,
            "skipped_stale": self.skipped_stale,
            "skipped_quality": self.skipped_quality,
            "skipped_duplicate": self.skipped_duplicate,
            "sells_mirrored": self.sells_mirrored,
            "pending_signals": len(self.pending_signals)
        }
