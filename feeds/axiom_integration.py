"""
Axiom Integration
Wires the Axiom scanner and wallet tracker into your existing bot.

HOW TO INTEGRATE INTO main.py
==============================

Step 1 — Add import (top of main.py):
    from feeds.axiom_integration import AxiomIntegration

Step 2 — After your Solana chain setup, add:
    axiom = AxiomIntegration(config=config)
    axiom.connect_to_bot(
        trader=sol_trader,
        signal_evaluator=signal_evaluator,     # from core/signal_evaluator.py
        security_checker=security,
        telegram=telegram,
        tracker=tracker,
        market_monitor=market_monitor,
        copy_trader=sol_copy,                  # optional
        edge_strategies=edge_strategies        # optional
    )
    tasks += axiom.get_tasks()

That's it. The Axiom scanner replaces DexScreener polling.
All your existing signal scoring, security checks, TP/SL, and
position management continue working exactly as before.

RAILWAY VARIABLES TO ADD
=========================
    AXIOM_EMAIL      your@email.com
    AXIOM_PASSWORD   yourpassword

Or if you have tokens from the browser:
    AXIOM_AUTH_TOKEN     eyJhbG...
    AXIOM_REFRESH_TOKEN  eyJhbG...
"""

import logging
from typing import Optional
from feeds.axiom_scanner import AxiomScanner, AxiomAuthManager
from feeds.axiom_trending_scanner import AxiomTrendingScanner, AxiomSurgeScanner
from feeds.axiom_smart_wallet_tracker import AxiomSmartWalletTracker
from feeds.axiom_price_feed import AxiomPriceFeed
from core.dip_watcher import DipWatcher

logger = logging.getLogger(__name__)


class AxiomIntegration:
    """
    One-stop integration class for all Axiom features.
    Manages auth, scanner, and wallet tracker together.
    """

    def __init__(self, config):
        """
        Initialize Axiom integration from your existing config object.
        Reads AXIOM_EMAIL and AXIOM_PASSWORD from environment if not in config.
        """
        # Auth manager — reads from config or environment variables
        self.auth = AxiomAuthManager(
            email=getattr(config, "axiom_email", ""),
            password=getattr(config, "axiom_password", ""),
            auth_token=getattr(config, "axiom_auth_token", ""),
            refresh_token=getattr(config, "axiom_refresh_token", "")
        )

        self.config  = config
        self.scanner: Optional[AxiomScanner] = None

        # Phase 1-4 additions
        self.trending_scanner: Optional[AxiomTrendingScanner] = None
        self.surge_scanner: Optional[AxiomSurgeScanner] = None
        self.wallet_tracker: Optional[AxiomSmartWalletTracker] = None
        self.price_feed: Optional[AxiomPriceFeed] = None
        self.dip_watcher: Optional[DipWatcher] = None

        self._tasks = []

    def connect_to_bot(self,
                        trader,
                        telegram,
                        tracker,
                        signal_evaluator=None,
                        security_checker=None,
                        market_monitor=None,
                        copy_trader=None,
                        edge_strategies=None,
                        scanner=None):
        """
        Wire Axiom into your existing bot components.

        Parameters:
            trader           — your Solana Trader instance
            telegram         — TelegramNotifier instance
            tracker          — PerformanceTracker instance
            signal_evaluator — TokenSignalEvaluator (optional but recommended)
            security_checker — SecurityChecker instance (optional)
            market_monitor   — MarketConditionMonitor instance (optional)
            copy_trader      — CopyTrader instance (optional)
                               If provided, Axiom wallet activity feeds into copy trading
            edge_strategies  — EdgeStrategies instance (optional)
                               If provided, qualified wallets auto-register into convergence
        """

        # Build scanner
        self.scanner = AxiomScanner(
            auth_manager=self.auth,
            trader=trader,
            signal_evaluator=signal_evaluator,
            security_checker=security_checker,
            telegram=telegram,
            tracker=tracker,
            market_monitor=market_monitor,
            min_mcap_usd=getattr(self.config, "min_mcap", 70_000),
            max_mcap_usd=float("inf"),  # no upper cap
            min_liquidity_usd=5_000, # $5k min liquidity
            min_score=getattr(self.config, "min_combined_score", 65.0),
            fallback_to_dexscreener=True,
            micro_cap_enabled=getattr(self.config, "micro_cap_enabled", True),
            micro_cap_min_usd=getattr(self.config, "micro_cap_min_mcap", 10_000),
            micro_cap_max_usd=getattr(self.config, "micro_cap_max_mcap", 80_000),
            micro_cap_position_usd=getattr(self.config, "micro_cap_position_usd", 80.0),
            micro_cap_max_snipers_pct=getattr(self.config, "micro_cap_max_snipers_pct", 15.0),
            micro_cap_max_dev_pct=getattr(self.config, "micro_cap_max_dev_pct", 10.0),
        )

        copy_wallets = getattr(self.config, "solana_copy_wallets", [])

        # ── Phase 1: Trending Scanner ─────────────────────────────────────────
        self.trending_scanner = AxiomTrendingScanner(
            auth_manager=self.auth,
            trader=trader,
            signal_evaluator=signal_evaluator,
            security_checker=security_checker,
            telegram=telegram,
            tracker=tracker,
            market_monitor=market_monitor,
            min_mcap_usd=getattr(self.config, "min_mcap", 70_000),
            min_liquidity_usd=5_000,
            min_score=getattr(self.config, "min_combined_score", 65.0),
            poll_interval=15,
            micro_cap_enabled=getattr(self.config, "micro_cap_enabled", True),
            micro_cap_min_usd=getattr(self.config, "micro_cap_min_mcap", 10_000),
            micro_cap_max_usd=getattr(self.config, "micro_cap_max_mcap", 80_000),
            micro_cap_position_usd=getattr(self.config, "micro_cap_position_usd", 80.0),
            micro_cap_max_snipers_pct=getattr(self.config, "micro_cap_max_snipers_pct", 15.0),
            micro_cap_max_dev_pct=getattr(self.config, "micro_cap_max_dev_pct", 10.0),
        )

        # ── Surge Scanner — established tokens with unusual activity spikes ──
        self.surge_scanner = AxiomSurgeScanner(
            auth_manager=self.auth,
            trader=trader,
            signal_evaluator=signal_evaluator,
            security_checker=security_checker,
            telegram=telegram,
            tracker=tracker,
            market_monitor=market_monitor,
            min_mcap_usd=getattr(self.config, "min_mcap", 100_000),
            min_liquidity_usd=10_000,
            min_score=getattr(self.config, "min_combined_score", 65.0),
            poll_interval=30,
            micro_cap_enabled=False,  # surge tokens are established, not micro-cap
        )

        # ── Phase 2: Smart Wallet WebSocket Tracker ───────────────────────────
        self.wallet_tracker = AxiomSmartWalletTracker(
            auth_manager=self.auth,
            trader=trader,
            signal_evaluator=signal_evaluator,
            security_checker=security_checker,
            telegram=telegram,
            tracker=tracker,
            market_monitor=market_monitor,
            wallets=copy_wallets,
            min_score=getattr(self.config, "min_combined_score", 65.0),
        )

        # ── Phase 4: Real-Time Price Feed ─────────────────────────────────────
        self.price_feed = AxiomPriceFeed(
            auth_manager=self.auth,
            trader=trader,
        )

        # ── DipWatcher — intercepts micro-cap buys, waits for dip+recovery ───
        self.dip_watcher = DipWatcher(
            price_feed=self.price_feed,
            trader=trader,
            dip_threshold_pct=getattr(self.config, "dip_watcher_threshold_pct", 30.0),
            recovery_pct=getattr(self.config, "dip_watcher_recovery_pct", 5.0),
            max_watch_seconds=getattr(self.config, "dip_watcher_max_seconds", 300.0),
        )
        # Inject into scanners so micro-cap buys route through dip/recovery gate
        self.scanner.dip_watcher          = self.dip_watcher
        self.trending_scanner.dip_watcher = self.dip_watcher
        # Wire chart analysis gate — all Axiom buy signals route through scanner
        # so they pass _chart_dip_check before any buy executes
        if scanner:
            self.scanner.scanner          = scanner
            self.trending_scanner.scanner = scanner
            self.surge_scanner.scanner    = scanner
            self.wallet_tracker.scanner   = scanner
            # Give DipWatcher the MultiSourceScanner so it can check _sl_cooldown/_pump_cooldown
            self.dip_watcher.scanner = scanner

        # Share price feed with the scanner — spike bonus AND stability gate
        self.scanner.price_feed       = self.price_feed
        self.scanner.axiom_price_feed = self.price_feed   # wire named attr used internally

        self._tasks = [
            self.scanner.run(),
            self.trending_scanner.run(),
            self.surge_scanner.run(),
            self.wallet_tracker.run(),
            self.price_feed.run(),
            self.dip_watcher._expire_watches(),
        ]

        logger.info(
            "[AxiomIntegration] Connected | "
            f"Scanner: {'real-time' if self.auth.has_credentials else 'fallback'}"
        )

    def get_tasks(self) -> list:
        """Return async tasks to add to main asyncio.gather()."""
        return self._tasks

    def get_stats(self) -> dict:
        stats = {"axiom": {}}
        if self.scanner:
            stats["axiom"]["scanner"] = self.scanner.get_stats()
        if self.trending_scanner:
            stats["axiom"]["trending_scanner"] = self.trending_scanner.get_stats()
        if self.surge_scanner:
            stats["axiom"]["surge_scanner"] = self.surge_scanner.get_stats()
        if self.wallet_tracker:
            stats["axiom"]["smart_wallet_tracker"] = self.wallet_tracker.get_stats()
        if self.price_feed:
            stats["axiom"]["price_feed"] = self.price_feed.get_stats()
        if self.dip_watcher:
            stats["axiom"]["dip_watcher"] = self.dip_watcher.get_stats()
        return stats
