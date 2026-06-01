"""
Solana Memecoin Bot v7 — Trader-Calibrated

All systems active with trader-specific rules:

TAKE PROFIT (your exact style):
  TP1: +10%  → sell 50% (lock in fast)
  TP2: +25%  → sell 75% of remaining (don't get greedy)
  TP3: +50%  → sell 100% (full exit)

STALL DETECTION (your rule):
  Volume drops below 20% of entry for 2x 30-min windows
  + position open at least 1 hour
  → Sell 75%, keep 25% moon bag

STOP LOSS:
  Hard -28% — no exceptions, no conditional skipping

AVERAGE DOWN (your rule):
  Only if < 15% loss AND volume still > 50% of entry
  One time only, add 50% of original size

MARKET CONDITIONS (your rule):
  BTC drops 5%+ → only score 85+ signals fire
  Score 90+ always fires regardless of conditions
  Resume when BTC stabilizes

Run:       python main.py
Dashboard: http://localhost:8080
Backtest:  python backtest/run_backtest.py
"""

import asyncio
import logging
import os
import time as _time
from pathlib import Path
from utils.config import Config
from utils.telegram_bot import TelegramNotifier
from core.risk_manager import RiskManager
from core.trader import Trader
from core.scalper import PositionScalper
from core.multi_source_scanner import MultiSourceScanner
from core.position_manager import PositionManager, MarketConditionMonitor
from chains.chain_config import SOLANA
from security.honeypot import SecurityChecker
from security.tax_detector import TaxDetector
from feeds.price_feed import PriceFeed
from feeds.solana_rpc_price_feed import SolanaRpcPriceFeed
from feeds.axiom_integration import AxiomIntegration
from feeds.graduation_sniper import GraduationSniper
from feeds.dip_scanner import DipScanner
from feeds.scalp_queue import ScalpQueue
from core.scalp_capital import ScalpCapitalManager
from analytics.wallet_scorer import WalletScorer
from analytics.kelly_sizer import KellySizer
from analytics.adaptive_threshold import AdaptiveThresholdManager
from onchain.solana_monitor import SolanaProgramMonitor
from sentiment.analyzer import SentimentAnalyzer
from ml.rug_classifier import RugClassifier
from execution.gas_oracle import GasOracle
from execution.kill_switch import KillSwitch, TelegramKillSwitchHandler
from execution.mev_protector import MEVProtector
from dashboard.tracker import PerformanceTracker
from dashboard.web_dashboard import WebDashboard
from core.strategies.cross_wallet_convergence import CrossWalletConvergenceStrategy
from core.strategies.wallet_clustering import WalletClusteringStrategy
from core.strategies.capitulation_reversal import CapitulationReversalStrategy
from core.realtime_signal import RealTimeSignalLayer
from core.bot_registry import BotRegistry
from core.bot_evaluator import BotEvaluator
from core.bot_manager import BotManager
from core.multi_bot_persistence import MultiBotTradeStore

MULTI_BOT_ENABLED = os.getenv("MULTI_BOT_ENABLED", "false").lower() == "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

_ANOMALY_NO_BUYS_HOURS   = 2.0    # alert if no buy in this many hours
_ANOMALY_SILENT_MINS     = 30     # alert if scanner evaluated=0 for this long
_WATCHDOG_INTERVAL_SECS  = 300    # check every 5 minutes


async def _anomaly_watchdog(scanners: list, price_feed, dashboard, telegram):
    """
    Background task: checks for persistent bot health problems every 5 minutes.
    Fires [ANOMALY] log lines (ERROR level) and appends to dashboard._anomaly_log.
    Covers the gaps where silent failures would otherwise go unnoticed.
    """
    await asyncio.sleep(60)  # let bot fully start before first check
    last_silent_alert: float = 0.0

    while True:
        await asyncio.sleep(_WATCHDOG_INTERVAL_SECS)
        now = _time.monotonic()
        anomalies = []

        for scanner in scanners:
            chain = getattr(getattr(scanner, "chain", None), "name", "unknown")

            # ── No buys in N hours ────────────────────────────────────────────
            last_buy = getattr(scanner, "_last_buy_time", 0)
            if last_buy > 0:
                hours_since = (now - last_buy) / 3600
                if hours_since >= _ANOMALY_NO_BUYS_HOURS:
                    anomalies.append(
                        f"[{chain}] No buy in {hours_since:.1f}h "
                        f"(signals_fired={getattr(scanner, 'signals_fired', 0)})"
                    )

            # ── Scanner evaluated=0 — completely silent ───────────────────────
            # signals_fired hasn't moved AND scanner has been running > SILENT_MINS
            # We check this by comparing signals_blocked_score (proxy for "scanner is running")
            # If both fired and all blocked counts are 0 and uptime > threshold, scanner may be stuck
            fired   = getattr(scanner, "signals_fired", 0)
            blocked = (getattr(scanner, "signals_blocked_score", 0) +
                       getattr(scanner, "signals_blocked_age", 0) +
                       getattr(scanner, "signals_blocked_security", 0))
            # tokens_evaluated is the multi-bot fleet's liveness signal (2026-05-29).
            # Without it this check false-fired whenever the legacy single-bot path
            # was quiet, even with the 121-bot fleet evaluating hundreds of tokens.
            evaluated = getattr(scanner, "tokens_evaluated", 0)
            uptime_mins = (now - getattr(scanner, "_start_monotonic", now)) / 60
            if fired == 0 and blocked == 0 and evaluated == 0 and uptime_mins > _ANOMALY_SILENT_MINS:
                if (now - last_silent_alert) > 3600:  # don't repeat within 1h
                    last_silent_alert = now
                    anomalies.append(
                        f"[{chain}] Scanner evaluated 0 tokens in {uptime_mins:.0f}min — "
                        f"may be stuck or misconfigured"
                    )

        # DexScreener WS anomaly check removed 2026-05-12 — the DS public
        # price WS was deprecated and the bot no longer attempts it.
        # Polling + Axiom WS canonically cover stops.

        # ── Fire alerts ───────────────────────────────────────────────────────
        for msg in anomalies:
            logger.error(f"[ANOMALY] {msg}")
            # Push to dashboard anomaly log (rolling 20)
            if hasattr(dashboard, "_anomaly_log"):
                dashboard._anomaly_log.append(msg)
                if len(dashboard._anomaly_log) > 20:
                    dashboard._anomaly_log.pop(0)


async def main():
    logger.info("=" * 60)
    logger.info("  Solana Memecoin Bot v7 — Trader Calibrated")
    logger.info("=" * 60)

    config = Config.load()

    # ── Multi-Bot Harness startup ────────────────────────────────────────
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    trade_store = MultiBotTradeStore(data_dir=data_dir)

    bot_manager = None
    if MULTI_BOT_ENABLED:
        # Run migration first (idempotent — safe to re-run on every boot)
        try:
            from scripts.migrate_trades_json_bot_id import migrate
            migrate(data_dir)
        except Exception as e:
            logger.warning(f"[main] migration failed (continuing): {e}")

        config_dir = Path(__file__).parent / "config" / "bots"

        # SP5 stale-cache reset (idempotent via /data/sp5_reset_done.json
        # sentinel). Recomputes each bot's capital state from post-cutoff
        # trades only, isolating the multi-bot fleet's accounting from
        # the first-deploy zombie buys. Safe to leave wired permanently —
        # the sentinel ensures it runs exactly once.
        try:
            from scripts.migrate_sp5_reset_balances import migrate as _sp5_migrate
            _sp5_migrate(data_dir=data_dir, config_dir=config_dir)
        except Exception as e:
            logger.warning(f"[main] sp5 reset failed (continuing): {e}")

        # Phantom-P&L scrub (2026-05-31): the PAPER_UNCAPPED overload degraded
        # the exit feed -> a bad SPCX tick booked +1180% phantom wins (~+$470
        # fake) on a few bots. Subtract phantom sells' fake pnl from affected
        # bots' bot_state (balance+realized) BEFORE the capital managers load
        # the snapshot, so /api/leaderboard is accurate. Sentinel'd (once),
        # backed up, bot_state-only (trades_multi left intact for audit).
        try:
            from scripts.scrub_phantom_pnl import migrate as _phantom_scrub
            _phantom_scrub(data_dir=data_dir)
        except Exception as e:
            logger.warning(f"[main] phantom scrub failed (continuing): {e}")

        # Mark the phantom SELL records in trades_multi.json (zero pnl, flag,
        # keep orig) so the trade list + future recomputes are clean too.
        # Independent of the bot_state scrub above (no double-correction).
        try:
            from scripts.scrub_phantom_pnl import mark_phantom_trades as _phantom_mark
            _phantom_mark(data_dir=data_dir)
        except Exception as e:
            logger.warning(f"[main] phantom trade-mark failed (continuing): {e}")

        registry = BotRegistry.from_directory(config_dir)
        evaluators = [BotEvaluator(c) for c in registry.configs]
        bot_manager = BotManager(evaluators=evaluators)
        logger.info(
            "[main] MULTI_BOT_ENABLED — loaded %d bots: %s",
            len(registry.configs),
            [c.bot_id for c in registry.configs],
        )
    else:
        logger.info("[main] MULTI_BOT_ENABLED=false — legacy single-bot only")

    logger.info(
        f"[Config] Effective settings:\n"
        f"  Capital: ${config.total_capital:.0f} | Daily loss limit: ${config.daily_loss_limit:.0f}\n"
        f"  Score: {config.min_combined_score} | Liquidity: ${config.min_liquidity_usd:,.0f}\n"
        f"  TP1: +{config.take_profit_1_pct}% → sell {config.take_profit_1_sell*100:.0f}%\n"
        f"  TP2: +{config.take_profit_2_pct}% → sell {config.take_profit_2_sell*100:.0f}%\n"
        f"  TP3: +{config.take_profit_3_pct}% → sell {config.take_profit_3_sell*100:.0f}%\n"
        f"  Stop loss: -{config.stop_loss_pct}% | Winner trail: -{config.winner_trail_pct}%\n"
        f"  Paper mode: {not bool(config.solana_private_key)}"
    )
    telegram = TelegramNotifier(config.telegram_token, config.telegram_chat_id)
    tracker = PerformanceTracker()
    dashboard = WebDashboard(port=config.dashboard_port, trade_store=trade_store)
    telegram.register_dashboard(dashboard)  # route all alerts → live event feed

    # ── Shared Systems ──────────────────────────────────────────────────
    security = SecurityChecker(
        max_buy_tax=config.max_buy_tax,
        max_sell_tax=config.max_sell_tax,
        max_top10_concentration=config.max_top10_concentration,
        max_dev_holding_pct=config.max_dev_holding_pct,
        block_mintable=config.block_mintable
    )
    tax_detector = TaxDetector(max_acceptable_tax=config.max_buy_tax)
    rug_classifier = RugClassifier(
        block_threshold=config.rug_block_threshold,
        caution_threshold=config.rug_caution_threshold
    )
    sentiment = SentimentAnalyzer(
        min_sentiment_score=config.min_sentiment_score,
        require_twitter=config.require_twitter
    )
    adaptive_threshold = AdaptiveThresholdManager(
        baseline_threshold=config.min_combined_score,
        target_win_rate=config.target_win_rate
    )
    kelly_sizer = KellySizer(
        total_capital=config.total_capital,
        kelly_fraction=config.kelly_fraction,
        min_position_pct=config.min_position_pct,
        max_position_pct=config.max_position_pct
    )
    price_feed = PriceFeed(
        helius_api_key=config.solana_rpc_url.split("api-key=")[-1]
        if "api-key=" in config.solana_rpc_url else ""
    )
    wallet_scorer = WalletScorer(
        min_win_rate=config.min_wallet_win_rate,
        min_trades_before_scoring=config.min_trades_before_scoring,
        max_consecutive_losses=config.max_consecutive_losses,
        pause_duration_minutes=config.wallet_pause_minutes,
        auto_block_after_pauses=config.auto_block_after_pauses
    )
    gas_oracle = GasOracle()

    # Market condition monitor — shared across all chains
    market_monitor = MarketConditionMonitor(
        btc_drop_threshold=config.btc_drop_threshold,
        restricted_score_threshold=config.restricted_score_threshold,
        override_score=config.override_score,
        normal_score_threshold=config.min_combined_score
    )

    kill_switch = KillSwitch(telegram=telegram)
    kill_handler = TelegramKillSwitchHandler(
        kill_switch=kill_switch,
        telegram=telegram,
        allowed_chat_ids=[config.telegram_chat_id]
    )

    # Alert Telegram when market conditions change
    async def on_market_restrict(reason):
        await telegram.send(
            f"⚠️ *Market Restricted*\n\n"
            f"📉 {reason}\n"
            f"🎯 Min score raised to {config.restricted_score_threshold}\n"
            f"✅ Score {config.override_score}+ still fires"
        )
    async def on_market_resume():
        await telegram.send(
            f"✅ *Market Conditions Normalized*\n\n"
            f"📈 BTC stable\n"
            f"🎯 Min score back to {config.min_combined_score}"
        )
    market_monitor.on_restrict(on_market_restrict)
    market_monitor.on_resume(on_market_resume)

    adaptive_threshold.register_chain("solana")

    dashboard.register_provider(tracker)
    tasks = []
    chain_summaries = []

    # ── SOLANA ──────────────────────────────────────────────────────────
    if config.enable_solana:
        sol_cap = config.total_capital

        sol_risk = RiskManager(sol_cap, config.max_position_pct,
                               config.daily_loss_limit,
                               max_position_usd=config.max_position_usd)
        dashboard.register_provider(sol_risk)
        SOLANA.rpc_url = config.solana_rpc_url
        sol_trader = Trader(config.solana_private_key, config.solana_rpc_url,
                            tracker, telegram, sol_risk,
                            stop_loss_pct=config.stop_loss_pct,
                            kill_switch=kill_switch)
        dashboard.register_trader(sol_trader)
        kill_switch.register_trader(sol_trader)

        for w in config.solana_copy_wallets:
            wallet_scorer.register_wallet(w, "solana")

        sol_scanner = MultiSourceScanner(
            chain=SOLANA, trader=sol_trader,
            security_checker=security, telegram=telegram,
            min_mcap=config.min_mcap, max_mcap=config.max_mcap,
            min_volume_h1_usd=config.min_volume_h1_usd,
            max_volume_h1_usd=config.max_volume_h1_usd,
            min_combined_score=config.min_combined_score,  # Hard floor — bypass adaptive threshold
            max_combined_score=config.max_combined_score,
            startup_delay=0,
            sentiment_analyzer=sentiment,
            rug_classifier=rug_classifier,
            tracker=tracker,
            scanner_keywords=config.scanner_keywords,
            chart_min_score=config.chart_min_score,
            chart_chaos_range_pct=config.chart_chaos_range_pct,
            chart_dead_vol_ratio=config.chart_dead_vol_ratio
        )
        # Load seed wallets from /data/seed_wallets.json (dashboard-managed)
        import json as _json, os as _os
        _seed_wallets_path = _os.path.join(_os.environ.get("DATA_DIR", "/data"), "seed_wallets.json")
        _seed_wallets: dict = {}
        try:
            with open(_seed_wallets_path) as _f:
                _seed_wallets = _json.load(_f)
            logger.info(f"[Main] Loaded {len(_seed_wallets)} seed wallets from {_seed_wallets_path}")
        except FileNotFoundError:
            logger.info("[Main] No seed_wallets.json found — starting fresh")
        except Exception as _e:
            logger.warning(f"[Main] Could not load seed_wallets.json: {_e}")

        sol_scalper = PositionScalper(
            chain_name="Solana", chain_id="solana",
            trader=sol_trader,
            open_positions_ref=sol_trader.open_positions,
            telegram=telegram, tracker=tracker,
            sell_trigger_pct=config.scalper_sell_trigger_pct,
            rebuy_trigger_pct=config.scalper_rebuy_trigger_pct,
            scalp_sell_pct=config.scalper_sell_pct,
            max_cycles_per_position=config.scalper_max_cycles,
            rebuy_window_hours=config.scalper_rebuy_window_hours,
            min_profit_usd=config.scalper_min_profit_usd,
            require_recovery_confirmation=config.scalper_require_recovery
        )
        sol_position_mgr = PositionManager(
            chain_name="Solana", chain_id="solana",
            trader=sol_trader,
            open_positions_ref=sol_trader.open_positions,
            telegram=telegram, tracker=tracker,
            market_monitor=market_monitor,
            tp1_pct=config.take_profit_1_pct,
            tp1_sell=config.take_profit_1_sell,
            tp2_pct=config.take_profit_2_pct,
            tp2_sell=config.take_profit_2_sell,
            tp3_pct=config.take_profit_3_pct,
            tp3_sell=config.take_profit_3_sell,
            stop_loss_pct=config.stop_loss_pct,
            winner_trail_pct=config.winner_trail_pct,
            stall_check_interval_min=config.stall_check_interval_min,
            stall_volume_threshold=config.stall_volume_threshold,
            stall_min_hours=config.stall_min_hours,
            stall_sell_pct=config.stall_sell_pct,
            avg_down_max_loss_pct=config.avg_down_max_loss_pct,
            avg_down_min_volume_pct=config.avg_down_min_volume_pct,
            avg_down_size_pct=config.avg_down_size_pct,
            mc_tp1_pct=config.mc_tp1_pct,
            mc_tp1_sell=config.mc_tp1_sell,
            mc_tp2_pct=config.mc_tp2_pct,
            mc_tp2_sell=config.mc_tp2_sell,
            mc_tp3_pct=config.mc_tp3_pct,
            mc_tp3_sell=config.mc_tp3_sell,
            mc_stop_loss_pct=config.mc_stop_loss_pct,
            mc_winner_trail_pct=config.mc_winner_trail_pct,
            dip_tp1_pct=config.dip_tp1_pct,
            dip_tp1_sell=config.dip_tp1_sell,
            dip_tp2_pct=config.dip_tp2_pct,
            dip_tp2_sell=config.dip_tp2_sell,
            dip_tp3_pct=config.dip_tp3_pct,
            dip_tp3_sell=config.dip_tp3_sell,
            dip_stop_pct=config.dip_stop_pct,
            dip_winner_trail_pct=config.dip_winner_trail_pct,
            scalp_tp1_pct=config.scalp_tp1_pct,
            scalp_tp1_sell=config.scalp_tp1_sell,
            scalp_tp2_pct=config.scalp_tp2_pct,
            scalp_tp2_sell=config.scalp_tp2_sell,
            scalp_stop_pct=config.scalp_stop_pct,
            scalp_time_exit_candles=config.scalp_time_exit_candles,
            scalp_time_exit_min_pct=config.scalp_time_exit_min_pct,
            scalp_max_hold_minutes=config.scalp_max_hold_minutes,
            scalper=sol_scalper,
            scanner=sol_scanner,
            # Share scanner's GT + DexScreener clients so the mid-hold
            # signal-flip detector reuses the same caches and rate limits.
            gt_client=getattr(sol_scanner, "gt_client", None),
            dexs_client=getattr(sol_scanner, "dexs_client", None),
        )
        kill_switch.register_scalper(sol_scalper)
        tracker.register_scalper(sol_scalper)

        # ── Real-Time Signal Layer ────────────────────────────────────────
        sol_rt_layer = RealTimeSignalLayer(
            chain_name="Solana",
            position_manager=sol_position_mgr
        )
        sol_scanner.realtime_signal_layer = sol_rt_layer

        helius_key = config.solana_rpc_url.split("api-key=")[-1] \
            if "api-key=" in config.solana_rpc_url else ""
        # SolanaProgramMonitor requires Helius WebSocket — disabled until Helius
        # quota is restored (constant 429s cause log spam and waste reconnect cycles)
        sol_monitor = None

        async def _auto_kill_check():
            while True:
                await asyncio.sleep(60)
                reason = kill_switch.check_auto_triggers(
                    sol_risk.daily_pnl, config.total_capital
                )
                if reason:
                    await kill_switch.trigger(reason)

        tasks += [
            sol_position_mgr.run(),
            sol_rt_layer.run(),
            _auto_kill_check()
        ]
        if config.scalper_enabled:
            tasks.append(sol_scalper.run())
            logger.info("[Main] PositionScalper enabled")
        else:
            logger.info("[Main] PositionScalper DISABLED (set SCALPER_ENABLED=true to re-enable)")
        if config.scanner_enabled:
            tasks.append(sol_scanner.run())
            logger.info("[Main] MultiSourceScanner enabled")
        else:
            logger.info("[Main] MultiSourceScanner disabled (SCANNER_ENABLED=false) — MSS polling loop not started")
        if sol_monitor:
            tasks.append(sol_monitor.run())

        # ── Graduation Sniper (wired after axiom init below) ─────────────
        grad_sniper = None
        if config.graduation_enabled:
            grad_sniper = GraduationSniper(
                rpc_url=config.solana_rpc_url,
                trader=sol_trader,
                position_usd=config.micro_cap_position_usd,
                max_price_impact_pct=10.0,
                sol_price_usd=150.0,
            )

        # ── Edge Strategies ──────────────────────────────────────────────
        sol_convergence = CrossWalletConvergenceStrategy(
            scanner=sol_scanner, telegram=telegram,
            helius_api_key="",  # Helius disabled — no credits
            wallet_quality_scores=_seed_wallets,
            poll_interval_sec=120,  # was 30s — cuts Helius usage 4x
        )
        sol_scanner._convergence_strategy = sol_convergence
        dashboard.register_scanner("solana", sol_scanner)

        sol_clustering = WalletClusteringStrategy(
            helius_api_key="",  # Helius disabled — no credits
            telegram=telegram,
            convergence_strategy=sol_convergence,
            min_cluster_score=60.0,
            rescan_interval_hours=4,
        )
        sol_capitulation = CapitulationReversalStrategy(
            scanner=sol_scanner, telegram=telegram,
            min_setup_quality=65.0, scan_interval_seconds=60,
        )
        tasks += [sol_convergence.run(), sol_clustering.run(), sol_capitulation.run()]

        dashboard.register_strategies(
            scanner=sol_scanner,
            scalper=sol_scalper,
            convergence=sol_convergence,
            clustering=sol_clustering,
            capitulation=sol_capitulation,
        )

        # ── Axiom Real-Time Feed ──────────────────────────────────────────
        axiom = AxiomIntegration(config=config)
        axiom.connect_to_bot(
            trader=sol_trader,
            telegram=telegram,
            tracker=tracker,
            signal_evaluator=sol_scanner.evaluator,   # reuse scanner's evaluator
            security_checker=security,                # reuse security checker
            market_monitor=market_monitor,
            edge_strategies=sol_convergence,
            # When scanner is disabled, don't route Axiom WS buys through it
            scanner=sol_scanner if config.scanner_enabled else None,
        )
        tasks += axiom.get_tasks()
        dashboard.register_axiom_auth(axiom.auth)
        sol_trader.register_security_checker(security)  # LP re-check at execution
        sol_trader.register_axiom_auth(axiom.auth)   # Axiom-first price lookups
        if axiom.price_feed:
            sol_trader.register_axiom_price_feed(axiom.price_feed)
            sol_rt_layer.ob_scorer._axiom_feed = axiom.price_feed
            sol_scanner.axiom_price_feed = axiom.price_feed
            axiom.price_feed.position_manager = sol_position_mgr  # event-driven stop loss
            sol_position_mgr.axiom_price_feed = axiom.price_feed  # fast price cache for mgmt cycle

        # Wire graduation sniper into Axiom feed — free graduation detection
        if grad_sniper is not None:
            axiom.set_graduation_sniper(grad_sniper)
            logger.info("[Main] Graduation sniper wired to Axiom WS feed")
        else:
            logger.info("[Main] Graduation sniper disabled (GRADUATION_ENABLED=false)")

        if config.dip_scanner_enabled:
            dip_scanner = DipScanner(
                trader=sol_trader,
                telegram=telegram,
                open_positions_ref=sol_trader.open_positions,
                position_usd=config.dip_position_usd,
                min_mcap=config.dip_min_mcap,
                max_mcap=config.dip_max_mcap,
                min_age_days=config.dip_min_age_days,
                min_volume_h24=config.dip_min_volume_h24,
                max_concurrent=config.dip_max_concurrent,
                min_txn_ratio_h6=config.dip_min_txn_ratio_h6,
                min_vol_h1_ratio=config.dip_min_vol_h1_ratio,
                require_vol_m5=config.dip_require_vol_m5,
                min_turnover_h24=config.dip_min_turnover_h24,
                baseline_mode=config.dip_baseline_mode,
                bot_manager=bot_manager,
                trade_store=trade_store,
            )
            # Tier 3: wire AxiomPriceFeed for sub-minute tick buffer reads at
            # signal-fire time. Optional — dip_scanner falls back to empty
            # tick_features if axiom_price_feed is None.
            if axiom is not None and getattr(axiom, "price_feed", None) is not None:
                dip_scanner.axiom_price_feed = axiom.price_feed
            # Register with dashboard so /api/user-watchlist endpoints can mutate it.
            dashboard.register_scanner("dip_scanner", dip_scanner)
            tasks.append(dip_scanner.run())
            logger.info(
                f"[Main] DipScanner enabled — "
                f"${config.dip_position_usd:.0f}/position, "
                f"mcap ${config.dip_min_mcap/1e6:.0f}M-${config.dip_max_mcap/1e6:.0f}M, "
                f"max {config.dip_max_concurrent} concurrent, "
                f"min bs_h6={config.dip_min_txn_ratio_h6:.2f}, "
                f"min turnover={config.dip_min_turnover_h24:.1f}x, "
                f"min vol_h1_ratio={config.dip_min_vol_h1_ratio:.2f} "
                f"(vol_m5_required={config.dip_require_vol_m5})"
            )

        if config.scalp_enabled:
            from feeds.gecko_ohlcv import GeckoTerminalClient
            scalp_capital = ScalpCapitalManager(
                total_capital=config.scalp_capital,
                max_position_usd=config.scalp_position_usd,
                max_concurrent=config.scalp_max_concurrent,
                daily_loss_limit=config.scalp_daily_loss_limit,
            )
            gt_client = GeckoTerminalClient(
                cache_ttl=config.scalp_gt_cache_ttl_sec,
                rate_per_min=config.scalp_gt_rate_per_min,
            )
            scalp_queue = ScalpQueue(
                trader=sol_trader,
                open_positions_ref=sol_trader.open_positions,
                scalp_capital=scalp_capital,
                config=config,
                ohlcv_client=gt_client,
                scanner=sol_scanner,
                auth_manager=axiom.auth,
            )
            sol_position_mgr.scalp_queue = scalp_queue
            dashboard.register_scalp_queue(scalp_queue, scalp_capital)
            tasks.append(scalp_queue.run())
            logger.info(
                f"[Main] ScalpQueue (4-phase) enabled — "
                f"${config.scalp_position_usd:.0f}/position, max={config.scalp_max_concurrent}, "
                f"TP1 +{config.scalp_tp1_pct}%/{int(config.scalp_tp1_sell*100)}%, "
                f"TP2 +{config.scalp_tp2_pct}%/{int(config.scalp_tp2_sell*100)}% of rem., "
                f"stop -{config.scalp_stop_pct}%"
            )

        # ── Breakout Strategy (Binance.US) ──────────────────────
        if config.breakout_enabled:
            from breakout.capital import BreakoutCapitalManager
            from breakout.data_client import BinanceUSClient
            from breakout.database import BreakoutDB
            from breakout.execution import BreakoutExecution
            from breakout.paper_fill import PaperFillEngine
            from breakout.scanner import BreakoutScanner
            from breakout.state import BreakoutState
            from breakout.strategy import BreakoutStrategy
            import os as _bk_os

            bk_state = BreakoutState()
            bk_capital = BreakoutCapitalManager(
                total_capital=config.breakout_capital,
                max_concurrent=config.breakout_max_concurrent,
            )
            bk_client = BinanceUSClient()
            bk_paper_fill = PaperFillEngine(
                data_client=bk_client,
                taker_fee=config.breakout_paper_taker_fee,
            )
            bk_data_dir = _bk_os.environ.get("DATA_DIR", ".")
            bk_db = BreakoutDB(_bk_os.path.join(bk_data_dir, "breakout.db"))

            bk_execution = BreakoutExecution(
                data_client=bk_client,
                paper_fill=bk_paper_fill,
                capital=bk_capital,
                state=bk_state,
                db=bk_db,
                config=config,
            )
            bk_scanner = BreakoutScanner(bk_client, bk_state, config)
            bk_strategy = BreakoutStrategy(bk_client, bk_state, config, bk_execution)

            dashboard.register_breakout(state=bk_state, capital=bk_capital, db=bk_db)

            tasks.append(bk_scanner.run())
            tasks.append(bk_strategy.run())
            tasks.append(bk_execution.run())

            logger.info(
                f"[Main] Breakout enabled — "
                f"${config.breakout_position_usd:.0f}/position, "
                f"TP +{config.breakout_tp_pct}%, stop -{config.breakout_stop_pct}%, "
                f"max {config.breakout_max_concurrent} concurrent"
            )
        else:
            logger.info("[Main] Breakout disabled (BREAKOUT_ENABLED=false)")

        # DexScreener real-time WebSocket feed — sub-second stop accuracy
        # price_feed is already started in tasks; wire it to the position manager
        # so every tick fires check_stop_loss_realtime() directly.
        price_feed.position_manager = sol_position_mgr
        sol_position_mgr.dex_price_feed = price_feed   # 1s-poll cache for price fallback
        sol_trader.register_dex_price_feed(price_feed)

        # Solana RPC + Jupiter price feed — covers ALL pool types at 0.5s intervals.
        # Eliminates the 5-15s DexScreener aggregator lag when Axiom WS isn't available.
        rpc_feed = SolanaRpcPriceFeed(rpc_url=config.solana_rpc_url)
        rpc_feed.position_manager = sol_position_mgr
        sol_position_mgr.rpc_price_feed = rpc_feed
        sol_trader.register_rpc_price_feed(rpc_feed)
        tasks.append(rpc_feed.run())

        # On-chain pool price feed (Helius WebSocket, decodes vault reserves
        # directly). Supports Raydium AMM v4 and pump.fun bonding curves;
        # falls through to existing feeds for pumpswap and other DEXes.
        # Solves the RAGEGUY 2026-05-15 issue where the DexScreener indexer
        # lagged a +13.5% real-pool spike, so the bot saw only +1.1% and
        # missed TPs that should have fired. Key extracted from RPC URL —
        # zero extra config required.
        try:
            from core.pool_price_feed import PoolPriceFeed
            _helius_key = ""
            _url = config.solana_rpc_url or ""
            if "api-key=" in _url:
                _helius_key = _url.split("api-key=")[-1].split("&")[0]
            if _helius_key:
                pool_feed = PoolPriceFeed(helius_api_key=_helius_key)
                pool_feed.position_manager = sol_position_mgr
                sol_trader.register_pool_price_feed(pool_feed)
                tasks.append(pool_feed.run())
                logger.info("[Main] PoolPriceFeed enabled (Helius WS, on-chain vault decode)")
            else:
                logger.info("[Main] PoolPriceFeed skipped — no Helius API key in RPC URL")
        except Exception as _e:
            logger.warning(f"[Main] PoolPriceFeed init error: {_e}")

        # Register AxiomScanner for relay mode token injection
        if axiom.scanner:
            dashboard.register_axiom_scanner(axiom.scanner)
        # Register EstablishedScanner for MC Radar panel
        if axiom.trending_scanner:
            dashboard.register_established_scanner(axiom.trending_scanner)

        # ── KOL Wallet Tracker ────────────────────────────────────────────
        # The tracker is already instantiated inside AxiomIntegration (axiom.wallet_tracker).
        # Wire it to seed_wallets.json so wallets added via the dashboard are picked up
        # on each reconnect without a full restart.
        if axiom.wallet_tracker:
            axiom.wallet_tracker.wallets_path = _seed_wallets_path
            axiom.wallet_tracker.min_score    = 0.0
            logger.info(
                f"[Main] KOL wallet tracker configured "
                f"({len(_seed_wallets)} wallets, min_score=0)"
            )

        chain_summaries.append(f"Solana — ${sol_cap:,.0f}")

    if not tasks:
        logger.error("No chains enabled in config.json")
        return

    # Collect all scanner instances for the anomaly watchdog
    all_scanners = list(dashboard._scanners.values())

    tasks += [
        price_feed.run(),
        market_monitor.run(),
        dashboard.run(),
        tracker.run_dashboard(),
        kill_handler.run(),
        _anomaly_watchdog(all_scanners, price_feed, dashboard, telegram),
    ]

    await telegram.send(
        "Solana Bot v7 Started\n\n"
        "Active strategy: dip_buy (real-dip-3 entry filter)\n"
        f"  TP1: +{config.dip_tp1_pct:.0f}% → sell {config.dip_tp1_sell*100:.0f}% of original\n"
        f"  TP2: +{config.dip_tp2_pct:.0f}% → sell {config.dip_tp2_sell*100:.0f}% of remainder\n"
        f"  TP3: +{config.dip_tp3_pct:.0f}% → sell {config.dip_tp3_sell*100:.0f}% (close)\n"
        f"  Stop: -{config.dip_stop_pct:.0f}% hard\n"
        f"  Entry gate: BLOCK if 5m > -3% AND 1h > -3% (no real pullback)\n"
        f"  Volume-death exit: sell on vol_m5=0 + vol_h1<$30k\n\n"
        + "\n".join(chain_summaries) + "\n\n"
        f"Capital: ${config.total_capital:,.0f} | "
        f"Daily limit: ${config.daily_loss_limit:,.0f}\n"
        "Commands: /kill /resume /status /help"
    )

    # Live-mode position reconciliation: fix any DB↔wallet drift that may
    # have happened during downtime (failed sells leaving ghost positions,
    # manual moves, etc.).  No-op in paper mode.
    if hasattr(sol_trader, "reconcile_positions_on_startup"):
        await sol_trader.reconcile_positions_on_startup()

    # Universe dip recorder — bundled as a daemon thread with its own asyncio
    # loop so its sync I/O (requests + time.sleep for GT rate-limit) can't
    # block the bot's event loop. Writes to RECORDER_DATA_DIR (default
    # /data/universe_recorder, falling back to .universe_recorder/ locally).
    # Gated by ENABLE_UNIVERSE_RECORDER env var — set to "false" to disable.
    import os as _os
    if _os.environ.get("ENABLE_UNIVERSE_RECORDER", "true").lower() in ("true", "1", "yes"):
        # Default the data dir to the Railway volume mount when it exists.
        if _os.path.isdir("/data") and not _os.environ.get("RECORDER_DATA_DIR"):
            _os.environ["RECORDER_DATA_DIR"] = "/data/universe_recorder"
        import threading as _threading
        def _recorder_runner():
            import asyncio as _asyncio
            from scripts.universe_dip_recorder import main as _recorder_main
            class _RecorderArgs:
                cycle_s = int(_os.environ.get("RECORDER_CYCLE_S", "120"))
                outcome_min = int(_os.environ.get("RECORDER_OUTCOME_MIN", "30"))
            try:
                _asyncio.run(_recorder_main(_RecorderArgs()))
            except Exception as e:
                logger.error(f"[UniverseRecorder] thread crashed: {e}")
        _rec_thread = _threading.Thread(
            target=_recorder_runner, daemon=True, name="universe_recorder"
        )
        _rec_thread.start()
        logger.info("[UniverseRecorder] thread started (daemon)")
    else:
        logger.info("[UniverseRecorder] disabled via ENABLE_UNIVERSE_RECORDER")

    logger.info(f"All systems go — {len(tasks)} tasks")
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
