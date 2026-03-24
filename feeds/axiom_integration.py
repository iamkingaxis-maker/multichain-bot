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
from feeds.axiom_wallet_tracker import AxiomWalletTracker
from feeds.axiom_trending_scanner import AxiomTrendingScanner
from feeds.axiom_smart_wallet_tracker import AxiomSmartWalletTracker
from feeds.axiom_price_feed import AxiomPriceFeed

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
        self.tracker: Optional[AxiomWalletTracker] = None

        # Phase 1-4 additions
        self.trending_scanner: Optional[AxiomTrendingScanner] = None
        self.wallet_tracker: Optional[AxiomSmartWalletTracker] = None
        self.price_feed: Optional[AxiomPriceFeed] = None

        self._tasks = []

    def connect_to_bot(self,
                        trader,
                        telegram,
                        tracker,
                        signal_evaluator=None,
                        security_checker=None,
                        market_monitor=None,
                        copy_trader=None,
                        edge_strategies=None):
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
            min_mcap_usd=70_000,     # $70k min — filter micro-cap rugs
            max_mcap_usd=float("inf"),  # no upper cap
            min_liquidity_usd=5_000, # $5k min liquidity
            min_score=getattr(self.config, "min_combined_score", 65.0),
            fallback_to_dexscreener=True
        )

        # Build wallet tracker
        self.tracker_instance = AxiomWalletTracker(auth_manager=self.auth)

        # Pre-load copy trading wallets into tracker
        copy_wallets = getattr(self.config, "solana_copy_wallets", [])
        if copy_wallets:
            self.tracker_instance.add_wallets(copy_wallets)
            logger.info(
                f"[AxiomIntegration] Tracking {len(copy_wallets)} "
                f"copy wallets for balance changes"
            )

        # Wire wallet activity → copy trader
        # When a copy wallet's SOL drops (they bought) → alert copy trader
        if copy_trader:
            async def on_wallet_activity(address, delta_sol, activity):
                if delta_sol < -0.1:  # Wallet spent SOL = bought tokens
                    logger.info(
                        f"[AxiomIntegration] Copy wallet {activity.label} "
                        f"bought something (spent {abs(delta_sol):.2f} SOL) — "
                        f"Helius will parse the transaction"
                    )
                    # Note: actual copy trade fires via Helius tx parsing
                    # This is an early warning that something happened

            self.tracker_instance.on_wallet_activity(on_wallet_activity)

        # Wire wallet tracker → edge strategies convergence
        if edge_strategies:
            async def on_wallet_qualified(address, activity):
                if activity.estimated_trades_today >= 3:
                    score = min(50 + activity.estimated_trades_today * 5, 90)
                    edge_strategies.add_known_wallet(address, score)
                    logger.info(
                        f"[AxiomIntegration] Active wallet {activity.label} "
                        f"added to convergence monitoring (score: {score})"
                    )

            self.tracker_instance.on_wallet_qualified(on_wallet_qualified)

        # ── Phase 1: Trending Scanner ─────────────────────────────────────────
        self.trending_scanner = AxiomTrendingScanner(
            auth_manager=self.auth,
            trader=trader,
            signal_evaluator=signal_evaluator,
            security_checker=security_checker,
            telegram=telegram,
            tracker=tracker,
            market_monitor=market_monitor,
            min_mcap_usd=70_000,
            min_liquidity_usd=5_000,
            min_score=getattr(self.config, "min_combined_score", 65.0),
            poll_interval=60,
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

        self._tasks = [
            self.scanner.run(),
            self.tracker_instance.run(),
            self.trending_scanner.run(),
            self.wallet_tracker.run(),
            self.price_feed.run(),
        ]

        logger.info(
            "[AxiomIntegration] Connected | "
            f"Scanner: {'real-time' if self.auth.has_credentials else 'fallback'} | "
            f"Wallet tracker: {len(copy_wallets)} wallets"
        )

    def get_tasks(self) -> list:
        """Return async tasks to add to main asyncio.gather()."""
        return self._tasks

    def get_stats(self) -> dict:
        stats = {"axiom": {}}
        if self.scanner:
            stats["axiom"]["scanner"] = self.scanner.get_stats()
        if hasattr(self, "tracker_instance"):
            stats["axiom"]["wallet_tracker"] = self.tracker_instance.get_stats()
        if self.trending_scanner:
            stats["axiom"]["trending_scanner"] = self.trending_scanner.get_stats()
        if self.wallet_tracker:
            stats["axiom"]["smart_wallet_tracker"] = self.wallet_tracker.get_stats()
        if self.price_feed:
            stats["axiom"]["price_feed"] = self.price_feed.get_stats()
        return stats
