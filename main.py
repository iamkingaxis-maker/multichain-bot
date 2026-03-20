"""
Multi-Chain Memecoin Bot v7 — Trader-Calibrated
Solana | Base | BNB Chain

All systems active with trader-specific rules:

TAKE PROFIT (your exact style):
  TP1: +50%  → sell 50% (lock in fast)
  TP2: +100% → sell 75% of remaining (don't get greedy)
  TP3: +150% → sell 75% of remaining (rare bonus)
  Moon bag: remainder rides indefinitely

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
Backtest:  python backtest/run_backtest.py --all-chains --find-optimal
"""

import asyncio
import logging
from utils.config import Config
from utils.telegram_bot import TelegramNotifier
from core.risk_manager import RiskManager
from core.trader import Trader
from core.copy_trader import CopyTrader
from core.scalper import PositionScalper
from core.multi_source_scanner import MultiSourceScanner, PumpFunMonitor
from core.position_manager import PositionManager, MarketConditionMonitor
from chains.chain_config import BASE, BNB, SOLANA
from chains.evm_trader import EVMTrader
from chains.evm_copy_trader import EVMCopyTrader
from security.honeypot import SecurityChecker
from security.tax_detector import TaxDetector
from feeds.price_feed import PriceFeed
from feeds.telegram_monitor import TelegramChannelMonitor
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


async def main():
    logger.info("=" * 60)
    logger.info("  Multi-Chain Memecoin Bot v7 — Trader Calibrated")
    logger.info("=" * 60)

    config = Config.load()
    telegram = TelegramNotifier(config.telegram_token, config.telegram_chat_id)
    tracker = PerformanceTracker()
    dashboard = WebDashboard(port=config.dashboard_port)

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
        require_twitter=config.require_twitter,
        lunarcrush_api_key=config.lunarcrush_api_key
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

    for chain_id in ["solana", "base", "bsc"]:
        adaptive_threshold.register_chain(chain_id)

    dashboard.register_provider(tracker)
    dashboard.register_provider(security)
    tasks = []
    chain_summaries = []
    sol_scanner = base_scanner = bnb_scanner = None

    # Normalize capital splits to only active chains
    active_chains = (
        (["solana"] if config.enable_solana else []) +
        (["base"]   if config.enable_base   else []) +
        (["bnb"]    if config.enable_bnb    else [])
    )
    raw = {k: config.capital_split.get(k, 0) for k in active_chains}
    total_raw = sum(raw.values()) or 1.0
    norm_split = {k: v / total_raw for k, v in raw.items()}

    # ── SOLANA ──────────────────────────────────────────────────────────
    if config.enable_solana:
        split = norm_split.get("solana", 1.0)
        sol_cap = config.total_capital * split

        sol_risk = RiskManager(sol_cap, config.max_position_pct,
                               config.daily_loss_limit * split)
        dashboard.register_provider(sol_risk)
        SOLANA.rpc_url = config.solana_rpc_url
        sol_trader = Trader(config.solana_private_key, config.solana_rpc_url,
                            tracker, telegram, sol_risk,
                            tp1=config.take_profit_1_pct / 100 + 1,
                            tp2=config.take_profit_2_pct / 100 + 1,
                            tp3=config.take_profit_3_pct / 100 + 1,
                            stop_loss=config.stop_loss_pct / 100)
        kill_switch.register_trader(sol_trader)
        tracker.register_trader(sol_trader)
        dashboard.register_sell_trader("solana", sol_trader)
        sol_trader.restore_positions()

        for w in config.solana_copy_wallets:
            wallet_scorer.register_wallet(w, "solana")

        sol_scanner = MultiSourceScanner(
            chain=SOLANA, trader=sol_trader,
            security_checker=security, telegram=telegram,
            birdeye_api_key=config.birdeye_api_key,
            min_mcap=config.min_mcap, max_mcap=config.max_mcap,
            min_liquidity_usd=config.min_liquidity_usd,
            min_volume_h1_usd=config.min_volume_h1_usd,
            min_combined_score=adaptive_threshold.get_threshold("solana"),
            max_combined_score=config.max_combined_score,
            require_both_sources=config.require_both_sources,
            min_holder_count=config.min_holder_count,
            single_source_min_score=config.single_source_min_score,
            max_dev_wallet_pct=config.max_dev_wallet_pct,
            pyramid_score_threshold=config.pyramid_score_threshold,
            preferred_age_min_hours=config.preferred_age_min_hours,
            preferred_age_max_hours=config.preferred_age_max_hours,
            hard_skip_age_hours=config.hard_skip_age_hours,
            rug_classifier=rug_classifier,
            tracker=tracker,
            startup_delay=0
        )
        if config.solanatracker_api_key:
            sol_scanner.set_solanatracker_key(config.solanatracker_api_key)
        dashboard.register_scanner("solana", sol_scanner)
        sol_copy = CopyTrader(
            wallets=config.solana_copy_wallets,
            trader=sol_trader, telegram=telegram, tracker=tracker,
            kelly_sizer=kelly_sizer,
            max_price_move_pct=config.copy_max_price_move_pct,
            min_hold_hours=config.copy_min_hold_hours,
            max_hold_hours=config.copy_max_hold_hours,
            min_win_rate=config.copy_min_win_rate,
            min_range_concentration=config.copy_min_range_concentration,
            copy_delay_seconds=config.copy_trade_delay_seconds
        )
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
            stall_check_interval_min=config.stall_check_interval_min,
            stall_volume_threshold=config.stall_volume_threshold,
            stall_min_hours=config.stall_min_hours,
            stall_sell_pct=config.stall_sell_pct,
            avg_down_max_loss_pct=config.avg_down_max_loss_pct,
            avg_down_min_volume_pct=config.avg_down_min_volume_pct,
            avg_down_size_pct=config.avg_down_size_pct,
            max_hold_hours=config.max_hold_hours
        )
        kill_switch.register_scalper(sol_scalper)
        tracker.register_scalper(sol_scalper)

        helius_key = config.solana_rpc_url.split("api-key=")[-1] \
            if "api-key=" in config.solana_rpc_url else ""
        sol_monitor = SolanaProgramMonitor(
            helius_api_key=helius_key,
            large_buy_threshold_sol=config.large_buy_threshold_sol
        ) if helius_key else None

        pump_monitor = PumpFunMonitor(scanner=sol_scanner, price_feed=price_feed)

        tasks += [
            sol_scanner.run(),
            sol_copy.run(),
            sol_scalper.run(),
            sol_position_mgr.run(),
            pump_monitor.run(),
            sol_trader.run_wallet_sync()
        ]
        if sol_monitor:
            tasks.append(sol_monitor.run())

        chain_summaries.append(f"Solana — ${sol_cap:,.0f}")

    # ── BASE ────────────────────────────────────────────────────────────
    if config.enable_base:
        split = norm_split.get("base", 0.30)
        base_cap = config.total_capital * split

        base_risk = RiskManager(base_cap, config.max_position_pct,
                                config.daily_loss_limit * split)
        dashboard.register_provider(base_risk)
        BASE.rpc_url = config.base_rpc_url
        base_trader = EVMTrader(BASE, config.evm_private_key, tracker,
                                telegram, base_risk,
                                config.take_profit_1_pct / 100 + 1,
                                config.take_profit_2_pct / 100 + 1,
                                config.take_profit_3_pct / 100 + 1,
                                config.stop_loss_pct / 100)
        kill_switch.register_trader(base_trader)
        tracker.register_trader(base_trader)
        dashboard.register_sell_trader("base", base_trader)

        for w in config.base_copy_wallets:
            wallet_scorer.register_wallet(w, "base")

        base_copy = EVMCopyTrader(
            chain=BASE, wallets=config.base_copy_wallets,
            trader=base_trader, telegram=telegram, tracker=tracker,
            kelly_sizer=kelly_sizer,
            max_price_move_pct=config.copy_max_price_move_pct,
            min_hold_hours=config.copy_min_hold_hours,
            max_hold_hours=config.copy_max_hold_hours,
            min_win_rate=config.copy_min_win_rate,
            min_range_concentration=config.copy_min_range_concentration,
            copy_delay_seconds=config.copy_trade_delay_seconds
        )
        base_copy.explorer_api_keys["base"] = config.basescan_api_key

        base_scanner = MultiSourceScanner(
            chain=BASE, trader=base_trader,
            security_checker=security, telegram=telegram,
            birdeye_api_key=config.birdeye_api_key,
            min_mcap=config.min_mcap, max_mcap=config.max_mcap,
            min_liquidity_usd=config.min_liquidity_usd,
            min_volume_h1_usd=config.min_volume_h1_usd,
            min_combined_score=adaptive_threshold.get_threshold("base"),
            max_combined_score=config.max_combined_score,
            require_both_sources=config.require_both_sources,
            single_source_min_score=config.single_source_min_score,
            max_dev_wallet_pct=config.max_dev_wallet_pct,
            pyramid_score_threshold=config.pyramid_score_threshold,
            preferred_age_min_hours=config.preferred_age_min_hours,
            preferred_age_max_hours=config.preferred_age_max_hours,
            hard_skip_age_hours=config.hard_skip_age_hours,
            rug_classifier=rug_classifier,
            tracker=tracker,
            startup_delay=20
        )
        dashboard.register_scanner("base", base_scanner)
        base_scalper = PositionScalper(
            chain_name="Base", chain_id="base",
            trader=base_trader,
            open_positions_ref=base_trader.open_positions,
            telegram=telegram, tracker=tracker,
            sell_trigger_pct=config.scalper_sell_trigger_pct,
            rebuy_trigger_pct=config.scalper_rebuy_trigger_pct,
            scalp_sell_pct=config.scalper_sell_pct,
            max_cycles_per_position=config.scalper_max_cycles,
            rebuy_window_hours=config.scalper_rebuy_window_hours,
            min_profit_usd=config.scalper_min_profit_usd,
            require_recovery_confirmation=config.scalper_require_recovery
        )
        base_position_mgr = PositionManager(
            chain_name="Base", chain_id="base",
            trader=base_trader,
            open_positions_ref=base_trader.open_positions,
            telegram=telegram, tracker=tracker,
            market_monitor=market_monitor,
            tp1_pct=config.take_profit_1_pct,
            tp1_sell=config.take_profit_1_sell,
            tp2_pct=config.take_profit_2_pct,
            tp2_sell=config.take_profit_2_sell,
            tp3_pct=config.take_profit_3_pct,
            tp3_sell=config.take_profit_3_sell,
            stop_loss_pct=config.stop_loss_pct,
            stall_check_interval_min=config.stall_check_interval_min,
            stall_volume_threshold=config.stall_volume_threshold,
            stall_min_hours=config.stall_min_hours,
            stall_sell_pct=config.stall_sell_pct,
            avg_down_max_loss_pct=config.avg_down_max_loss_pct,
            avg_down_min_volume_pct=config.avg_down_min_volume_pct,
            avg_down_size_pct=config.avg_down_size_pct,
            max_hold_hours=config.max_hold_hours
        )
        kill_switch.register_scalper(base_scalper)
        tracker.register_scalper(base_scalper)

        tasks += [
            base_scanner.run(),
            base_copy.run(),
            base_scalper.run(),
            base_position_mgr.run()
        ]
        chain_summaries.append(f"Base — ${base_cap:,.0f}")

    # ── BNB ─────────────────────────────────────────────────────────────
    if config.enable_bnb:
        split = norm_split.get("bnb", 0.20)
        bnb_cap = config.total_capital * split

        bnb_risk = RiskManager(bnb_cap, config.max_position_pct,
                               config.daily_loss_limit * split)
        dashboard.register_provider(bnb_risk)
        BNB.rpc_url = config.bnb_rpc_url
        bnb_trader = EVMTrader(BNB, config.evm_private_key, tracker,
                               telegram, bnb_risk,
                               config.take_profit_1_pct / 100 + 1,
                               config.take_profit_2_pct / 100 + 1,
                               config.take_profit_3_pct / 100 + 1,
                               config.stop_loss_pct / 100)
        kill_switch.register_trader(bnb_trader)
        tracker.register_trader(bnb_trader)
        dashboard.register_sell_trader("bnb chain", bnb_trader)

        for w in config.bnb_copy_wallets:
            wallet_scorer.register_wallet(w, "bnb")

        bnb_copy = EVMCopyTrader(
            chain=BNB, wallets=config.bnb_copy_wallets,
            trader=bnb_trader, telegram=telegram, tracker=tracker,
            kelly_sizer=kelly_sizer,
            max_price_move_pct=config.copy_max_price_move_pct,
            min_hold_hours=config.copy_min_hold_hours,
            max_hold_hours=config.copy_max_hold_hours,
            min_win_rate=config.copy_min_win_rate,
            min_range_concentration=config.copy_min_range_concentration,
            copy_delay_seconds=config.copy_trade_delay_seconds
        )
        bnb_copy.explorer_api_keys["bsc"] = config.bscscan_api_key

        bnb_scanner = MultiSourceScanner(
            chain=BNB, trader=bnb_trader,
            security_checker=security, telegram=telegram,
            birdeye_api_key=config.birdeye_api_key,
            min_mcap=config.min_mcap, max_mcap=config.max_mcap,
            min_liquidity_usd=config.min_liquidity_usd,
            min_volume_h1_usd=config.min_volume_h1_usd,
            min_combined_score=adaptive_threshold.get_threshold("bsc"),
            max_combined_score=config.max_combined_score,
            require_both_sources=config.require_both_sources,
            single_source_min_score=config.single_source_min_score,
            max_dev_wallet_pct=config.max_dev_wallet_pct,
            pyramid_score_threshold=config.pyramid_score_threshold,
            preferred_age_min_hours=config.preferred_age_min_hours,
            preferred_age_max_hours=config.preferred_age_max_hours,
            hard_skip_age_hours=config.hard_skip_age_hours,
            rug_classifier=rug_classifier,
            tracker=tracker,
            startup_delay=40
        )
        dashboard.register_scanner("bnb", bnb_scanner)
        bnb_scalper = PositionScalper(
            chain_name="BNB", chain_id="bsc",
            trader=bnb_trader,
            open_positions_ref=bnb_trader.open_positions,
            telegram=telegram, tracker=tracker,
            sell_trigger_pct=config.scalper_sell_trigger_pct,
            rebuy_trigger_pct=config.scalper_rebuy_trigger_pct + 5.0,
            scalp_sell_pct=config.scalper_sell_pct,
            max_cycles_per_position=config.scalper_max_cycles,
            rebuy_window_hours=config.scalper_rebuy_window_hours,
            min_profit_usd=config.scalper_min_profit_usd * 2,
            require_recovery_confirmation=config.scalper_require_recovery
        )
        bnb_position_mgr = PositionManager(
            chain_name="BNB", chain_id="bsc",
            trader=bnb_trader,
            open_positions_ref=bnb_trader.open_positions,
            telegram=telegram, tracker=tracker,
            market_monitor=market_monitor,
            tp1_pct=config.take_profit_1_pct,
            tp1_sell=config.take_profit_1_sell,
            tp2_pct=config.take_profit_2_pct,
            tp2_sell=config.take_profit_2_sell,
            tp3_pct=config.take_profit_3_pct,
            tp3_sell=config.take_profit_3_sell,
            stop_loss_pct=config.stop_loss_pct,
            stall_check_interval_min=config.stall_check_interval_min,
            stall_volume_threshold=config.stall_volume_threshold,
            stall_min_hours=config.stall_min_hours,
            stall_sell_pct=config.stall_sell_pct,
            avg_down_max_loss_pct=config.avg_down_max_loss_pct,
            avg_down_min_volume_pct=config.avg_down_min_volume_pct,
            avg_down_size_pct=config.avg_down_size_pct,
            max_hold_hours=config.max_hold_hours
        )
        kill_switch.register_scalper(bnb_scalper)
        tracker.register_scalper(bnb_scalper)

        tasks += [
            bnb_scanner.run(),
            bnb_copy.run(),
            bnb_scalper.run(),
            bnb_position_mgr.run()
        ]
        chain_summaries.append(f"BNB — ${bnb_cap:,.0f}")

    if not tasks:
        logger.error("No chains enabled in config.json")
        return

    # ── Telegram Alpha Channel Monitor ──────────────────────────────────
    tg_monitor = TelegramChannelMonitor(
        api_id=config.telegram_api_id,
        api_hash=config.telegram_api_hash,
        session_string=config.telegram_session,
        channels=config.telegram_monitor_channels,
        sol_scanner=sol_scanner  if config.enable_solana else None,
        base_scanner=base_scanner if config.enable_base   else None,
        bnb_scanner=bnb_scanner  if config.enable_bnb    else None,
    )
    if config.telegram_session and config.telegram_monitor_channels:
        tasks.append(tg_monitor.run())
        logger.info(
            f"[TelegramMonitor] Enabled — "
            f"{len(config.telegram_monitor_channels)} channels"
        )

    # ── Cloudflare Bypass — disabled: Railway IPs are blocked at IP level by Cloudflare
    # io.dexscreener.com returns "Attention Required!" for datacenter IPs regardless of
    # browser fingerprinting. Playwright cannot solve an IP-level block.
    # Keeping the code but skipping launch to avoid wasting a Chromium instance.
    logger.info("CF bypass: skipped — io.dexscreener.com IP-blocks Railway datacenter addresses")

    tasks += [
        price_feed.run(),
        market_monitor.run(),
        dashboard.run(),
        tracker.run_dashboard(),
        kill_handler.run()
    ]

    await telegram.send(
        "Multi-Chain Bot v7 Started — Trader Calibrated\n\n"
        "Your exact trading rules loaded:\n"
        f"  TP1: +{config.take_profit_1_pct:.0f}% → sell {config.take_profit_1_sell*100:.0f}%\n"
        f"  TP2: +{config.take_profit_2_pct:.0f}% → sell {config.take_profit_2_sell*100:.0f}% of rest\n"
        f"  TP3: +{config.take_profit_3_pct:.0f}% → sell {config.take_profit_3_sell*100:.0f}% of rest\n"
        f"  Stop: -{config.stop_loss_pct:.0f}% hard\n"
        f"  Stall: sell 75% if volume dead 1h+\n"
        f"  Avg down: only if <{config.avg_down_max_loss_pct:.0f}% loss + volume ok\n"
        f"  BTC guard: restrict at -{config.btc_drop_threshold:.0f}%\n\n"
        + "\n".join(chain_summaries) + "\n\n"
        f"Capital: ${config.total_capital:,.0f} | "
        f"Daily limit: ${config.daily_loss_limit:,.0f}\n"
        "Commands: /kill /resume /status /help"
    )

    logger.info(f"All systems go — {len(tasks)} tasks")
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
