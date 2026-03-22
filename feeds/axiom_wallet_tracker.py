"""
Axiom Wallet Tracker
Supercharges wallet clustering and copy trading with Axiom's
batched balance API — check hundreds of wallets in one request.

Current bot approach:  Helius, one wallet at a time
Axiom approach:        100 wallets per request, 5 concurrent batches
                       = 500 wallets scored in the time it took to check 1

Integration:
    from feeds.axiom_wallet_tracker import AxiomWalletTracker

    wallet_tracker = AxiomWalletTracker(auth_manager=axiom_auth)
    tasks.append(wallet_tracker.run())

    # Feed qualified wallets into your convergence strategy:
    wallet_tracker.on_wallet_qualified(
        lambda addr, score: edge_strategies.add_known_wallet(addr, score)
    )
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable, Set
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    from axiomtradeapi import AxiomTradeClient
    from axiomtradeapi.batch import BatchProcessor
    AXIOM_AVAILABLE = True
except ImportError:
    AXIOM_AVAILABLE = False


@dataclass
class WalletSnapshot:
    """Point-in-time balance snapshot for a wallet."""
    address: str
    sol_balance: float
    lamports: int
    slot: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.timestamp).total_seconds()


@dataclass
class WalletActivity:
    """Activity tracking for a monitored wallet."""
    address: str
    label: str

    snapshots: List[WalletSnapshot] = field(default_factory=list)
    balance_changes: List[float] = field(default_factory=list)  # SOL deltas

    # Inferred activity
    last_buy_detected: Optional[datetime] = None
    last_sell_detected: Optional[datetime] = None
    estimated_trades_today: int = 0
    active_today: bool = False

    @property
    def latest_balance(self) -> float:
        if not self.snapshots:
            return 0.0
        return self.snapshots[-1].sol_balance

    @property
    def balance_trend(self) -> float:
        """Recent balance change in SOL."""
        if len(self.snapshots) < 2:
            return 0.0
        return self.snapshots[-1].sol_balance - self.snapshots[-2].sol_balance

    def add_snapshot(self, snap: WalletSnapshot):
        self.snapshots.append(snap)
        if len(self.snapshots) > 48:  # Keep 24h at 30min intervals
            self.snapshots = self.snapshots[-48:]

        if len(self.snapshots) >= 2:
            delta = self.snapshots[-1].sol_balance - self.snapshots[-2].sol_balance
            self.balance_changes.append(delta)
            if len(self.balance_changes) > 48:
                self.balance_changes = self.balance_changes[-48:]

            # Infer buy/sell activity from balance changes
            # SOL balance drops = bought tokens, SOL balance rises = sold tokens
            if delta < -0.1:  # Spent SOL (bought tokens)
                self.last_buy_detected = datetime.now(timezone.utc)
                self.estimated_trades_today += 1
                self.active_today = True
            elif delta > 0.1:  # Received SOL (sold tokens)
                self.last_sell_detected = datetime.now(timezone.utc)
                self.estimated_trades_today += 1
                self.active_today = True


class AxiomWalletTracker:
    """
    Tracks wallet balances at scale using Axiom's batched API.

    Primary uses:
      1. Monitor copy trading wallets for activity signals
      2. Verify wallet clustering candidates are actually active
      3. Detect when a tracked wallet is buying (SOL balance drops)
         before the on-chain transaction is parsed

    This gives you a fast early-warning system: if a wallet's SOL
    balance suddenly drops, they just bought something — check
    what they bought via Helius transaction parsing.
    """

    def __init__(self,
                 auth_manager,
                 poll_interval_seconds: int = 60,
                 batch_size: int = 100,
                 concurrent_batches: int = 5):

        self.auth             = auth_manager
        self.poll_interval    = poll_interval_seconds
        self.batch_size       = batch_size
        self.concurrent_batches = concurrent_batches

        self._client: Optional[AxiomTradeClient] = None
        self._processor: Optional[BatchProcessor] = None

        # Wallet tracking
        self._wallets: Dict[str, WalletActivity] = {}
        self._qualified_callbacks: List[Callable] = []
        self._activity_callbacks: List[Callable] = []

        # Stats
        self.polls_completed  = 0
        self.wallets_checked  = 0
        self.activity_detected = 0

    def add_wallet(self, address: str, label: str = ""):
        """Register a wallet for balance tracking."""
        if address not in self._wallets:
            label = label or f"{address[:6]}...{address[-4:]}"
            self._wallets[address] = WalletActivity(address=address, label=label)
            logger.debug(f"[AxiomWalletTracker] Added wallet: {label}")

    def add_wallets(self, addresses: List[str]):
        """Register multiple wallets at once."""
        for addr in addresses:
            self.add_wallet(addr)

    def on_wallet_qualified(self, callback: Callable):
        """
        Register callback for when a wallet shows consistent activity.
        Signature: callback(address: str, activity: WalletActivity)
        """
        self._qualified_callbacks.append(callback)

    def on_wallet_activity(self, callback: Callable):
        """
        Register callback for any wallet balance change.
        Fires immediately when a wallet's SOL balance changes significantly.
        Signature: callback(address: str, delta_sol: float, activity: WalletActivity)
        """
        self._activity_callbacks.append(callback)

    async def run(self):
        """Main polling loop — checks all wallets every 60 seconds."""
        if not AXIOM_AVAILABLE:
            logger.warning(
                "[AxiomWalletTracker] axiomtradeapi not installed. "
                "pip install axiomtradeapi"
            )
            return

        logger.info(
            f"[AxiomWalletTracker] Started | "
            f"Polling {len(self._wallets)} wallets every {self.poll_interval}s"
        )

        while True:
            try:
                await self._poll_all_wallets()
            except Exception as e:
                logger.error(f"[AxiomWalletTracker] Poll error: {e}")
            await asyncio.sleep(self.poll_interval)

    async def _poll_all_wallets(self):
        """Poll all tracked wallets in batches."""
        if not self._wallets:
            return

        # Ensure valid auth
        token_valid = await self.auth.ensure_valid_token()
        if not token_valid:
            logger.warning("[AxiomWalletTracker] No valid auth token")
            return

        # Initialize or refresh client
        if not self._client:
            self._client = AxiomTradeClient(
                auth_token=self.auth.auth_token,
                refresh_token=self.auth.refresh_token
            )
            self._processor = BatchProcessor(self._client)

        addresses = list(self._wallets.keys())

        try:
            # Batch balance check — up to 100 wallets per request
            results = await self._processor.process_wallets(
                addresses,
                batch_size=self.batch_size,
                concurrent_batches=self.concurrent_batches
            )

            self.polls_completed += 1
            self.wallets_checked += len(addresses)

            # Process results
            for addr, balance_data in results.items():
                if balance_data is None:
                    continue
                await self._update_wallet(addr, balance_data)

        except Exception as e:
            logger.error(f"[AxiomWalletTracker] Batch error: {e}")
            # Reset client on error
            self._client = None
            self._processor = None

    async def _update_wallet(self, address: str, balance_data: dict):
        """Update a wallet's activity record with new balance data."""
        activity = self._wallets.get(address)
        if not activity:
            return

        snap = WalletSnapshot(
            address=address,
            sol_balance=balance_data.get("sol", 0),
            lamports=balance_data.get("lamports", 0),
            slot=balance_data.get("slot", 0)
        )

        prev_balance = activity.latest_balance
        activity.add_snapshot(snap)

        # Check for significant balance change
        delta = snap.sol_balance - prev_balance
        if abs(delta) > 0.1 and len(activity.snapshots) > 1:
            self.activity_detected += 1
            action = "BOUGHT" if delta < 0 else "SOLD"
            logger.info(
                f"[AxiomWalletTracker] 📊 {activity.label} "
                f"{action} | Delta: {delta:+.2f} SOL"
            )

            # Fire activity callbacks
            for cb in self._activity_callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(address, delta, activity)
                    else:
                        cb(address, delta, activity)
                except Exception as e:
                    logger.debug(f"[AxiomWalletTracker] Callback error: {e}")

    async def get_wallet_balance(self, address: str) -> float:
        """Get current SOL balance for a single wallet."""
        if not AXIOM_AVAILABLE or not self._client:
            return 0.0
        try:
            result = self._client.GetBalance(address)
            return result.get("sol", 0)
        except Exception:
            return 0.0

    async def get_batch_balances(self,
                                  addresses: List[str]) -> Dict[str, float]:
        """Get SOL balances for multiple wallets in one request."""
        if not AXIOM_AVAILABLE:
            return {}

        token_valid = await self.auth.ensure_valid_token()
        if not token_valid:
            return {}

        if not self._client:
            self._client = AxiomTradeClient(
                auth_token=self.auth.auth_token,
                refresh_token=self.auth.refresh_token
            )

        try:
            results = self._client.GetBatchedBalance(addresses)
            return {
                addr: data.get("sol", 0)
                for addr, data in results.items()
                if data is not None
            }
        except Exception as e:
            logger.error(f"[AxiomWalletTracker] Batch balance error: {e}")
            return {}

    def get_most_active_wallets(self, n: int = 10) -> List[WalletActivity]:
        """Return the n most active wallets by estimated trade count."""
        return sorted(
            self._wallets.values(),
            key=lambda w: w.estimated_trades_today,
            reverse=True
        )[:n]

    def get_stats(self) -> dict:
        return {
            "wallets_tracked":    len(self._wallets),
            "polls_completed":    self.polls_completed,
            "wallets_checked":    self.wallets_checked,
            "activity_detected":  self.activity_detected,
            "active_today":       sum(
                1 for w in self._wallets.values() if w.active_today
            )
        }
