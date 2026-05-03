"""
Multi-Chain Bot v13 Configuration
Supports two modes:
  1. Local development: reads from config.json
  2. Railway deployment: reads from environment variables

Railway environment variables take priority over config.json.
This means you never put private keys in config.json on Railway —
they go in Railway's Variables tab instead.
"""

import json
import os
from dataclasses import dataclass, field
from typing import List


def env(key: str, default=None):
    """Read from environment variable, falling back to default."""
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


def env_float(key: str, default: float = 0.0) -> float:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def env_int(key: str, default: int = 0) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def env_list(key: str, default: list = None) -> list:
    """Read comma-separated list from environment variable."""
    val = os.environ.get(key)
    if not val:
        return default or []
    return [v.strip() for v in val.split(",") if v.strip()]


@dataclass
class Config:
    # ── Wallets ──────────────────────────────────────────────
    solana_private_key: str = ""
    scalper_solana_private_key: str = ""

    # ── RPC Endpoints ────────────────────────────────────────
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"

    # ── API Keys ─────────────────────────────────────────────
    birdeye_api_key: str = ""

    # ── Axiom ────────────────────────────────────────────────
    axiom_email: str = ""
    axiom_password: str = ""
    axiom_auth_token: str = ""
    axiom_refresh_token: str = ""

    # ── Telegram ─────────────────────────────────────────────
    telegram_token: str = ""
    telegram_chat_id: str = ""
    dashboard_port: int = 8080

    # ── Chains ───────────────────────────────────────────────
    enable_solana: bool = True

    # ── Capital ──────────────────────────────────────────────
    # Kelly criterion at 18% win rate → 1-2% per trade. Using 2.5% ($50 at
    # $2k capital) to account for win-rate uncertainty while staying Kelly-safe.
    total_capital: float = 2000.0
    max_position_pct: float = 0.025   # 2.5% of capital per trade = ~$50
    max_position_usd: float = 50.0    # hard cap (was $200)
    min_position_pct: float = 0.02
    daily_loss_limit: float = 200.0

    # ── Take Profit ──────────────────────────────────────────
    # TP1 at +35% sell 50%: reachable on real runners, captures capital.
    # Winner trail (pre-TP1, fires at 10%+ peak) handles tokens that peak below TP1.
    take_profit_1_pct: float = 35.0   # +35% → sell 50%, let 50% run to TP2/TP3
    take_profit_1_sell: float = 0.50
    take_profit_2_pct: float = 100.0
    take_profit_2_sell: float = 0.50
    take_profit_3_pct: float = 300.0
    take_profit_3_sell: float = 1.0

    # ── Micro-Cap Take Profit (separate tiers for fresh launches) ──────
    # MC tokens are extreme high-volatility — either 2x-10x or die.
    # TP1 at 2x (100%) sell 50%: break-even drops to ~13% (profitable at 18%).
    mc_tp1_pct: float = 100.0       # 2x → sell 50%, let 50% run to TP2/TP3
    mc_tp1_sell: float = 0.50
    mc_tp2_pct: float = 300.0       # 4x → sell 50% of remaining
    mc_tp2_sell: float = 0.50
    mc_tp3_pct: float = 900.0       # 10x → sell everything
    mc_tp3_sell: float = 1.0
    mc_stop_loss_pct: float = 25.0  # Wider — PumpSwap graduates routinely dip 20-30% on normal post-grad volatility
    mc_winner_trail_pct: float = 15.0  # Trail 15% from peak (more room for volatility)

    # ── Stop Loss ────────────────────────────────────────────
    stop_loss_pct: float = 25.0

    # ── Winner Protection ────────────────────────────────────
    winner_trail_pct: float = 15.0  # Close 100% if drops 15% from peak after TP1

    # ── DipWatcher ───────────────────────────────────────────
    dip_watcher_threshold_pct: float = 20.0  # % drop from peak to declare dipped (was 30%)

    # ── Stall Detection ──────────────────────────────────────
    stall_check_interval_min: int = 5
    stall_volume_threshold: float = 0.20
    stall_min_hours: float = 0.1   # allow stall check after ~6 min
    stall_sell_pct: float = 1.0

    # ── Average Down ─────────────────────────────────────────
    avg_down_max_loss_pct: float = 4.0
    avg_down_min_volume_pct: float = 0.50
    avg_down_size_pct: float = 0.50

    # ── Market Conditions ────────────────────────────────────
    btc_drop_threshold: float = 5.0
    restricted_score_threshold: int = 85
    override_score: int = 90

    # ── Global Pause ─────────────────────────────────────────
    trading_paused: bool = False  # TRADING_PAUSED env — blocks all new buys across strategies; open positions close naturally

    # ── Scanner ──────────────────────────────────────────────
    scanner_enabled: bool = False  # disabled — 60 trades, 28% WR, -$198 P&L; gates MSS polling + Axiom buy-routing
    # Disables AxiomScanner + AxiomTrendingScanner + AxiomSurgeScanner +
    # AxiomSmartWalletTracker run loops. AxiomPriceFeed stays alive (DipScanner
    # uses its tick buffer). Buys from these scanners would already be blocked
    # by STRATEGY_ALLOWLIST=dip_buy at the trader; this flag stops the wasted
    # scan cycles + noise (rugcheck calls, log spam) when only dip_buy is active.
    axiom_scanners_enabled: bool = False
    min_mcap: float = 80_000
    max_mcap: float = 999_999_999  # No upper cap — scanner evaluates all sizes above min_mcap
    max_volume_h1_usd: float = 300_000

    # ── Dip Buyer ────────────────────────────────────────────────
    dip_scanner_enabled: bool = True  # 118 trades, 52% WR, +$574 P&L — proven profitable strategy
    dip_position_usd: float = 500.0        # Fixed position size
    dip_min_mcap: float = 1_000_000        # $1M minimum mcap
    dip_max_mcap: float = 100_000_000      # $100M max FDV — excludes BONK/PUMP-tier large caps that don't bounce
    dip_min_age_days: float = 0.0          # No age floor — other filters (bs_h6, turnover, vol-decay) do structural protection. Still blocks tokens with missing pairCreatedAt.
    dip_min_volume_h24: float = 200_000    # $200k minimum 24h volume
    dip_tp1_pct: float = 8.0              # TP at +8% — sell 100%. Lowered 2026-05-02 from 12% (user directive). Tighter exit reduces give-back on tokens that touch +8% but reverse before +12%.
    dip_tp1_sell: float = 1.0             # Sell entire position at TP (was 0.50 partial). Runner trail dropped — see asymmetric_exit_analysis.py.
    dip_tp2_pct: float = 15.0             # TP2 unreachable when TP1 sells 100% — left as a safety guard.
    dip_tp2_sell: float = 1.0
    dip_stop_pct: float = 8.0             # Hard stop at -8% (was 10.0 — TP/stop counterfactual sweep on post-bf0a596 data: TP=+12% / stop=-8% improves total $ by +$10 on n=64 with same 52% WR, while -10% returned -$7.62 and -6% returned +$5.80 with WR drop to 47%. -8% is the WR-preserving improvement. Mechanism: 23 deep losers (max_dd <= -10) crystallize at -8% instead of -10%, saving $0.40 each (~$9.20 total); 6 mid-band trades (-10 < dd <= -8) affected mildly. 2026-05-02.)
    dip_winner_trail_pct: float = 3.5     # Trail kept as field but unused — post-TP1 trail block in position_manager dropped 2026-05-01 (no moonshots in sample, trail gave back 6.67pp avg).
    dip_max_concurrent: int = 4           # Max simultaneous dip positions
    dip_min_txn_ratio_h6: float = 1.3     # require h6 buy/sell txn ratio >= 1.3 (blocks distribution: DUMBMONEY 1.11, SPIKE 1.20; passes WIFE 1.54, BULL 1.53)
    dip_min_vol_h1_ratio: float = 0.5     # require vol_h1 >= vol_h24/48 (= 50% of avg hourly rate). Blocks decelerating-volume tokens (67, TROLL); passes BULL 0.80x, pippin 0.72x
    dip_require_vol_m5: bool = True       # require vol_m5 > 0 (blocks fully dead tokens)
    dip_min_turnover_h24: float = 2.0     # require vol_h24 / liquidity >= 2.0 — blocks over-liquid tokens that don't move (pippin 0.9x, TROLL 0.5x, 67 1.3x); passes all winners (BULL 3.9x lowest)
    # Baseline data-collection mode (DIP_BASELINE_MODE env). When true, all
    # heuristic/structural dip filters are bypassed — only basic sanity gates
    # (mcap, age, vol_h24, vol_m5, already_open, loss_cooldown, max_concurrent)
    # still enforce. Verdicts are still computed and logged so we can correlate
    # forward outcomes with chart_reader features and individual filter
    # verdicts. Intended for paper-mode shadow runs to gather a population
    # sample across the FULL signal space; not for live use.
    dip_baseline_mode: bool = False

    # ── Scalp Strategy (4-phase setup detector: impulse/pullback/sweep/reclaim) ──
    scalp_enabled: bool = False  # disabled — 17 trades, -$22 total, re-enable after rewrite lands
    scalp_capital: float = 2000.0
    scalp_position_usd: float = 200.0
    scalp_max_concurrent: int = 5           # spec max (was 10 in dip-buy era)
    scalp_daily_loss_limit: float = 400.0
    scalp_max_deployment_pct: float = 0.80  # 60–80% cap per spec — use upper bound

    # Entry (setup detector)
    scalp_impulse_min_pct: float = 10.0
    scalp_impulse_max_pct: float = 30.0
    scalp_impulse_lookback: int = 6
    scalp_pullback_min_pct: float = 30.0
    scalp_pullback_max_pct: float = 60.0
    scalp_sweep_vol_mult: float = 1.5
    scalp_sweep_vol_lookback: int = 20
    scalp_min_rr: float = 2.0

    # Exits
    scalp_tp1_pct: float = 10.0             # +10% → sell 50%
    scalp_tp1_sell: float = 0.50
    scalp_tp2_pct: float = 15.0             # +15% → sell 35% of remaining
    scalp_tp2_sell: float = 0.35
    scalp_stop_pct: float = 6.0             # hard stop — spec max
    scalp_time_exit_candles: int = 4        # 3–5 candle window — use midpoint
    scalp_time_exit_min_pct: float = 5.0    # need +5% within time_exit_candles or exit
    scalp_max_hold_minutes: float = 45.0    # absolute safety belt

    # Market selection (candidate gates)
    scalp_min_m5_volume_usd: float = 5_000
    scalp_min_liquidity_usd: float = 30_000
    scalp_min_age_minutes: int = 5
    scalp_max_age_hours: float = 24.0
    scalp_rug_lp_drop_pct: float = 10.0
    scalp_max_watch_candidates: int = 40
    scalp_watch_expiry_minutes: float = 30.0
    scalp_stop_cooldown_minutes: float = 45.0

    # GeckoTerminal
    scalp_gt_rate_per_min: int = 10
    scalp_gt_cache_ttl_sec: int = 180
    scalp_gt_trending_pages: int = 1

    # ── Breakout Strategy (Binance.US) ───────────────────────
    breakout_enabled: bool = False              # BREAKOUT_ENABLED — independent kill switch
    breakout_capital: float = 2000.0
    breakout_position_usd: float = 500.0
    breakout_max_concurrent: int = 4
    breakout_cooldown_minutes: float = 45.0
    breakout_min_score: int = 7
    # exits
    breakout_tp_pct: float = 4.0
    breakout_tp_sell_pct: float = 0.50
    breakout_stop_pct: float = 3.0
    breakout_trail_pct: float = 2.0
    breakout_max_hold_hours: float = 4.0
    # scanner / watchlist
    breakout_scan_interval_min: float = 10.0
    breakout_scan_top_n: int = 200
    breakout_min_vol_24h_usd: float = 50_000_000
    breakout_change_24h_min_pct: float = 3.0
    breakout_change_24h_max_pct: float = 15.0
    breakout_change_6h_max_pct: float = 12.0
    breakout_watchlist_size: int = 5
    breakout_excluded_bases: List[str] = field(
        default_factory=lambda: ["USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "GUSD", "PYUSD"]
    )
    # poll / timing
    breakout_poll_interval_sec: float = 30.0
    breakout_candle_close_delay_sec: float = 2.0
    # paper fill
    breakout_paper_taker_fee: float = 0.006     # 0.6% Binance.US retail taker
    # market regime (BTC-driven)
    breakout_regime_symbol: str = "BTCUSDT"
    breakout_regime_red_1h_pct: float = -1.0    # BTC 1h change < this → red
    breakout_regime_risk_off_drop_pct: float = 2.0  # BTC 15m candle drop >= this → risk-off (block new entries)
    breakout_red_min_score: int = 8             # entry score floor in red market
    breakout_red_min_vol_ratio: float = 1.5     # breakout candle vol ratio floor in red market
    breakout_red_watchlist_size: int = 3        # trim watchlist to this in red market

    # ── Micro-Cap Mode (AxiomScanner only) ───────────────────
    # Targets fresh $10k-$50k pairs via Axiom WS with tighter gates
    micro_cap_enabled: bool = False  # Disabled — rug risk too high; graduation sniper covers fresh tokens
    micro_cap_min_mcap: float = 10_000
    micro_cap_max_mcap: float = 80_000  # Raised from 50k — covers PumpSwap graduates (~$67k mcap)
    micro_cap_position_usd: float = 40.0

    # ── Graduation Sniper (fresh pump.fun graduates via Axiom WS) ──
    graduation_enabled: bool = False  # disabled — 0 fills in paper (divide-by-zero on un-indexed tokens); scanner-only consolidation
    micro_cap_max_snipers_pct: float = 15.0   # block if snipers hold > 15%
    micro_cap_max_dev_pct: float = 10.0        # block if dev holds > 10%
    min_volume_h1_usd: float = 15_000
    min_combined_score: int = 65
    max_combined_score: int = 100

    # ── Chart quality (OHLCV TA filter) ──────────────────────────────────
    chart_min_score: int = 10           # 0-30; below this = don't buy
    chart_chaos_range_pct: float = 30.0 # block if 6-candle range > this %
    chart_dead_vol_ratio: float = 0.3   # block if VAR (recent/prior vol) < this
    require_both_sources: bool = True
    min_liquidity_usd: float = 10_000
    max_dev_wallet_pct: float = 5.0
    hard_skip_age_hours: float = 999.0
    pyramid_score_threshold: int = 90
    enable_pyramids: bool = False  # Disabled — 18% win rate, structurally buys at the top
    volume_acceleration_candles: int = 3

    # ── Security ─────────────────────────────────────────────
    max_buy_tax: float = 10.0
    max_sell_tax: float = 10.0
    max_top10_concentration: float = 95.0
    max_dev_holding_pct: float = 15.0
    block_mintable: bool = True
    base_slippage: float = 2.0
    rug_block_threshold: float = 0.60
    rug_caution_threshold: float = 0.40

    # ── Copy Trading ─────────────────────────────────────────
    min_wallet_win_rate: float = 0.50
    min_trades_before_scoring: int = 5
    max_consecutive_losses: int = 5
    wallet_pause_minutes: int = 60
    auto_block_after_pauses: int = 3
    solana_copy_wallets: List[str] = field(default_factory=list)
    copy_trade_delay_seconds: int = 5
    copy_min_trade_size_native: float = 0.01
    copy_max_price_move_pct: float = 15.0
    copy_min_hold_hours: float = 1.0
    copy_max_hold_hours: float = 4.0
    copy_min_win_rate: float = 0.50
    copy_min_range_concentration: float = 0.50

    # ── Scalper ──────────────────────────────────────────────
    # Disabled by default (2026-04-27) — price feed glitches were causing
    # phantom triggers. Upside sanity gate added to position_manager but
    # leaving scalper disabled until proven needed for dip_buy strategy.
    scalper_enabled: bool = False
    scalper_sell_trigger_pct: float = 25.0
    scalper_rebuy_trigger_pct: float = 20.0
    scalper_sell_pct: float = 0.25
    scalper_max_cycles: int = 4
    scalper_rebuy_window_hours: float = 2.0
    scalper_min_profit_usd: float = 5.0
    scalper_require_recovery: bool = True

    # ── Analytics ────────────────────────────────────────────
    kelly_fraction: float = 0.50
    target_win_rate: float = 0.55
    min_sentiment_score: int = 20
    require_twitter: bool = False
    use_flashbots: bool = True
    large_buy_threshold_sol: float = 5.0

    # ── Scanner Keywords ──────────────────────────────────────
    scanner_keywords: List[str] = field(default_factory=lambda: [
        "solana", "sol meme", "new launch", "pump", "moon",
        "pepe", "doge", "cat", "ai", "trump"
    ])

    @classmethod
    def load(cls, path: str = "config.json") -> "Config":
        """
        Load config from environment variables first (Railway),
        falling back to config.json for local development.
        """
        # Start with defaults
        config = cls()

        # Load from config.json if it exists (local dev)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                for key, value in data.items():
                    if hasattr(config, key):
                        setattr(config, key, value)
            except Exception as e:
                print(f"Warning: Could not load config.json: {e}")

        # Environment variables OVERRIDE config.json
        # This is how Railway injects secrets safely
        _apply_env_overrides(config)

        _validate(config)
        return config


def _apply_env_overrides(config: Config):
    """
    Apply Railway environment variable overrides.
    Variable names match config keys in UPPER_CASE.

    Set these in Railway's Variables tab:
      SOLANA_PRIVATE_KEY
      TELEGRAM_TOKEN
      TELEGRAM_CHAT_ID
      SOLANA_RPC_URL
      BIRDEYE_API_KEY
      (and any others you want to override)
    """
    _paper_mode = os.environ.get("PAPER_MODE", "").lower() in ("1", "true", "yes")

    # Wallet keys — most sensitive, always from env on Railway
    if os.environ.get("SOLANA_PRIVATE_KEY"):
        config.solana_private_key = env("SOLANA_PRIVATE_KEY")
    if os.environ.get("SCALPER_SOLANA_PRIVATE_KEY"):
        config.scalper_solana_private_key = env("SCALPER_SOLANA_PRIVATE_KEY")

    # Paper mode applied LAST so it always wins over the private key env vars above
    if _paper_mode:
        config.solana_private_key = ""
        config.scalper_solana_private_key = ""

    # RPC URLs
    if os.environ.get("SOLANA_RPC_URL"):
        config.solana_rpc_url = env("SOLANA_RPC_URL")

    # API keys
    if os.environ.get("BIRDEYE_API_KEY"):
        config.birdeye_api_key = env("BIRDEYE_API_KEY")

    # Axiom credentials
    if os.environ.get("AXIOM_EMAIL"):
        config.axiom_email = env("AXIOM_EMAIL")
    if os.environ.get("AXIOM_PASSWORD"):
        config.axiom_password = env("AXIOM_PASSWORD")
    if os.environ.get("AXIOM_AUTH_TOKEN"):
        config.axiom_auth_token = env("AXIOM_AUTH_TOKEN")
    if os.environ.get("AXIOM_REFRESH_TOKEN"):
        config.axiom_refresh_token = env("AXIOM_REFRESH_TOKEN")

    # Telegram
    if os.environ.get("TELEGRAM_TOKEN"):
        config.telegram_token = env("TELEGRAM_TOKEN")
    if os.environ.get("TELEGRAM_CHAT_ID"):
        config.telegram_chat_id = env("TELEGRAM_CHAT_ID")

    # Scanner score threshold
    if os.environ.get("MIN_COMBINED_SCORE"):
        config.min_combined_score = env_int("MIN_COMBINED_SCORE", config.min_combined_score)
    if os.environ.get("MAX_COMBINED_SCORE"):
        config.max_combined_score = env_int("MAX_COMBINED_SCORE", config.max_combined_score)

    # Scanner mcap and volume filters
    if os.environ.get("MIN_MCAP"):
        config.min_mcap = env_float("MIN_MCAP", config.min_mcap)
    if os.environ.get("MAX_MCAP"):
        config.max_mcap = env_float("MAX_MCAP", config.max_mcap)
    if os.environ.get("MAX_VOLUME_H1_USD"):
        config.max_volume_h1_usd = env_float("MAX_VOLUME_H1_USD", config.max_volume_h1_usd)

    # Capital settings (optional overrides)
    if os.environ.get("TOTAL_CAPITAL"):
        config.total_capital = env_float("TOTAL_CAPITAL", config.total_capital)
    GAS_RESERVE_USD = 10.0
    config.total_capital = max(0.0, config.total_capital - GAS_RESERVE_USD)
    if os.environ.get("DAILY_LOSS_LIMIT"):
        config.daily_loss_limit = env_float("DAILY_LOSS_LIMIT", config.daily_loss_limit)

    # Risk / exit settings
    if os.environ.get("STOP_LOSS_PCT"):
        config.stop_loss_pct = env_float("STOP_LOSS_PCT", config.stop_loss_pct)
    if os.environ.get("AVG_DOWN_MAX_LOSS_PCT"):
        config.avg_down_max_loss_pct = env_float("AVG_DOWN_MAX_LOSS_PCT", config.avg_down_max_loss_pct)
    if os.environ.get("WINNER_TRAIL_PCT"):
        config.winner_trail_pct = env_float("WINNER_TRAIL_PCT", config.winner_trail_pct)
    if os.environ.get("TAKE_PROFIT_1_PCT"):
        config.take_profit_1_pct = env_float("TAKE_PROFIT_1_PCT", config.take_profit_1_pct)
    if os.environ.get("TAKE_PROFIT_1_SELL"):
        config.take_profit_1_sell = env_float("TAKE_PROFIT_1_SELL", config.take_profit_1_sell)
    if os.environ.get("MC_TP1_PCT"):
        config.mc_tp1_pct = env_float("MC_TP1_PCT", config.mc_tp1_pct)
    if os.environ.get("MC_TP1_SELL"):
        config.mc_tp1_sell = env_float("MC_TP1_SELL", config.mc_tp1_sell)

    # Chain toggle
    if os.environ.get("ENABLE_SOLANA"):
        config.enable_solana = env_bool("ENABLE_SOLANA", config.enable_solana)

    # Copy wallets (comma-separated in env)
    if os.environ.get("SOLANA_COPY_WALLETS"):
        config.solana_copy_wallets = env_list("SOLANA_COPY_WALLETS")

    # Dashboard port (Railway assigns this automatically)
    if os.environ.get("PORT"):
        config.dashboard_port = env_int("PORT", config.dashboard_port)

    # Global pause — blocks all new buys
    if os.environ.get("TRADING_PAUSED"):
        config.trading_paused = env_bool("TRADING_PAUSED", config.trading_paused)

    # MultiSourceScanner (legacy poll-based scanner — disabled by default)
    if os.environ.get("SCANNER_ENABLED"):
        config.scanner_enabled = env_bool("SCANNER_ENABLED", config.scanner_enabled)
    # Axiom scanner suite (Trending/Surge/SmartWallet/AxiomScanner) — disabled by default
    if os.environ.get("AXIOM_SCANNERS_ENABLED"):
        config.axiom_scanners_enabled = env_bool("AXIOM_SCANNERS_ENABLED", config.axiom_scanners_enabled)

    # Dip scanner
    if os.environ.get("DIP_SCANNER_ENABLED"):
        config.dip_scanner_enabled = env_bool("DIP_SCANNER_ENABLED", config.dip_scanner_enabled)
    if os.environ.get("DIP_POSITION_USD"):
        config.dip_position_usd = env_float("DIP_POSITION_USD", config.dip_position_usd)
    if os.environ.get("DIP_MIN_MCAP"):
        config.dip_min_mcap = env_float("DIP_MIN_MCAP", config.dip_min_mcap)
    if os.environ.get("DIP_MAX_MCAP"):
        config.dip_max_mcap = env_float("DIP_MAX_MCAP", config.dip_max_mcap)
    if os.environ.get("DIP_MIN_AGE_DAYS"):
        config.dip_min_age_days = env_float("DIP_MIN_AGE_DAYS", config.dip_min_age_days)
    if os.environ.get("DIP_MIN_TXN_RATIO_H6"):
        config.dip_min_txn_ratio_h6 = env_float(
            "DIP_MIN_TXN_RATIO_H6", config.dip_min_txn_ratio_h6
        )
    if os.environ.get("DIP_MIN_VOL_H1_RATIO"):
        config.dip_min_vol_h1_ratio = env_float(
            "DIP_MIN_VOL_H1_RATIO", config.dip_min_vol_h1_ratio
        )
    if os.environ.get("DIP_REQUIRE_VOL_M5"):
        config.dip_require_vol_m5 = env_bool(
            "DIP_REQUIRE_VOL_M5", config.dip_require_vol_m5
        )
    if os.environ.get("DIP_MIN_TURNOVER_H24"):
        config.dip_min_turnover_h24 = env_float(
            "DIP_MIN_TURNOVER_H24", config.dip_min_turnover_h24
        )
    if os.environ.get("DIP_MIN_VOLUME_H24"):
        config.dip_min_volume_h24 = env_float("DIP_MIN_VOLUME_H24", config.dip_min_volume_h24)
    if os.environ.get("DIP_STOP_PCT"):
        config.dip_stop_pct = env_float("DIP_STOP_PCT", config.dip_stop_pct)
    if os.environ.get("DIP_WINNER_TRAIL_PCT"):
        config.dip_winner_trail_pct = env_float(
            "DIP_WINNER_TRAIL_PCT", config.dip_winner_trail_pct
        )
    if os.environ.get("DIP_MAX_CONCURRENT"):
        config.dip_max_concurrent = env_int(
            "DIP_MAX_CONCURRENT", config.dip_max_concurrent
        )
    if os.environ.get("DIP_BASELINE_MODE"):
        config.dip_baseline_mode = env_bool(
            "DIP_BASELINE_MODE", config.dip_baseline_mode
        )
    if os.environ.get("SCALPER_ENABLED"):
        config.scalper_enabled = env_bool("SCALPER_ENABLED", config.scalper_enabled)

    # Scalp queue
    if os.environ.get("SCALP_ENABLED"):
        config.scalp_enabled = env_bool("SCALP_ENABLED", config.scalp_enabled)
    if os.environ.get("SCALP_CAPITAL"):
        config.scalp_capital = env_float("SCALP_CAPITAL", config.scalp_capital)
    if os.environ.get("SCALP_POSITION_USD"):
        config.scalp_position_usd = env_float("SCALP_POSITION_USD", config.scalp_position_usd)
    if os.environ.get("SCALP_STOP_PCT"):
        config.scalp_stop_pct = env_float("SCALP_STOP_PCT", config.scalp_stop_pct)
    if os.environ.get("SCALP_MAX_CONCURRENT"):
        config.scalp_max_concurrent = env_int("SCALP_MAX_CONCURRENT", config.scalp_max_concurrent)
    if os.environ.get("SCALP_DAILY_LOSS_LIMIT"):
        config.scalp_daily_loss_limit = env_float("SCALP_DAILY_LOSS_LIMIT", config.scalp_daily_loss_limit)
    if os.environ.get("SCALP_TP1_PCT"):
        config.scalp_tp1_pct = env_float("SCALP_TP1_PCT", config.scalp_tp1_pct)
    if os.environ.get("SCALP_TP2_PCT"):
        config.scalp_tp2_pct = env_float("SCALP_TP2_PCT", config.scalp_tp2_pct)
    if os.environ.get("SCALP_MIN_RR"):
        config.scalp_min_rr = env_float("SCALP_MIN_RR", config.scalp_min_rr)
    if os.environ.get("SCALP_TIME_EXIT_CANDLES"):
        config.scalp_time_exit_candles = env_int("SCALP_TIME_EXIT_CANDLES", config.scalp_time_exit_candles)
    if os.environ.get("SCALP_MIN_M5_VOLUME_USD"):
        config.scalp_min_m5_volume_usd = env_float("SCALP_MIN_M5_VOLUME_USD", config.scalp_min_m5_volume_usd)
    if os.environ.get("SCALP_MIN_LIQUIDITY_USD"):
        config.scalp_min_liquidity_usd = env_float("SCALP_MIN_LIQUIDITY_USD", config.scalp_min_liquidity_usd)

    # Breakout strategy
    if os.environ.get("BREAKOUT_ENABLED"):
        config.breakout_enabled = env_bool("BREAKOUT_ENABLED", config.breakout_enabled)
    if os.environ.get("BREAKOUT_CAPITAL"):
        config.breakout_capital = env_float("BREAKOUT_CAPITAL", config.breakout_capital)
    if os.environ.get("BREAKOUT_POSITION_USD"):
        config.breakout_position_usd = env_float("BREAKOUT_POSITION_USD", config.breakout_position_usd)
    if os.environ.get("BREAKOUT_MAX_CONCURRENT"):
        config.breakout_max_concurrent = env_int("BREAKOUT_MAX_CONCURRENT", config.breakout_max_concurrent)
    if os.environ.get("BREAKOUT_COOLDOWN_MINUTES"):
        config.breakout_cooldown_minutes = env_float("BREAKOUT_COOLDOWN_MINUTES", config.breakout_cooldown_minutes)
    if os.environ.get("BREAKOUT_MIN_SCORE"):
        config.breakout_min_score = env_int("BREAKOUT_MIN_SCORE", config.breakout_min_score)
    if os.environ.get("BREAKOUT_TP_PCT"):
        config.breakout_tp_pct = env_float("BREAKOUT_TP_PCT", config.breakout_tp_pct)
    if os.environ.get("BREAKOUT_TP_SELL_PCT"):
        config.breakout_tp_sell_pct = env_float("BREAKOUT_TP_SELL_PCT", config.breakout_tp_sell_pct)
    if os.environ.get("BREAKOUT_STOP_PCT"):
        config.breakout_stop_pct = env_float("BREAKOUT_STOP_PCT", config.breakout_stop_pct)
    if os.environ.get("BREAKOUT_TRAIL_PCT"):
        config.breakout_trail_pct = env_float("BREAKOUT_TRAIL_PCT", config.breakout_trail_pct)
    if os.environ.get("BREAKOUT_MAX_HOLD_HOURS"):
        config.breakout_max_hold_hours = env_float("BREAKOUT_MAX_HOLD_HOURS", config.breakout_max_hold_hours)
    if os.environ.get("BREAKOUT_SCAN_INTERVAL_MIN"):
        config.breakout_scan_interval_min = env_float("BREAKOUT_SCAN_INTERVAL_MIN", config.breakout_scan_interval_min)
    if os.environ.get("BREAKOUT_MIN_VOL_24H_USD"):
        config.breakout_min_vol_24h_usd = env_float("BREAKOUT_MIN_VOL_24H_USD", config.breakout_min_vol_24h_usd)
    if os.environ.get("BREAKOUT_PAPER_TAKER_FEE"):
        config.breakout_paper_taker_fee = env_float("BREAKOUT_PAPER_TAKER_FEE", config.breakout_paper_taker_fee)
    if os.environ.get("BREAKOUT_CHANGE_24H_MIN_PCT"):
        config.breakout_change_24h_min_pct = env_float("BREAKOUT_CHANGE_24H_MIN_PCT", config.breakout_change_24h_min_pct)
    if os.environ.get("BREAKOUT_CHANGE_24H_MAX_PCT"):
        config.breakout_change_24h_max_pct = env_float("BREAKOUT_CHANGE_24H_MAX_PCT", config.breakout_change_24h_max_pct)
    if os.environ.get("BREAKOUT_REGIME_RED_1H_PCT"):
        config.breakout_regime_red_1h_pct = env_float("BREAKOUT_REGIME_RED_1H_PCT", config.breakout_regime_red_1h_pct)
    if os.environ.get("BREAKOUT_REGIME_RISK_OFF_DROP_PCT"):
        config.breakout_regime_risk_off_drop_pct = env_float("BREAKOUT_REGIME_RISK_OFF_DROP_PCT", config.breakout_regime_risk_off_drop_pct)
    if os.environ.get("BREAKOUT_RED_MIN_SCORE"):
        config.breakout_red_min_score = env_int("BREAKOUT_RED_MIN_SCORE", config.breakout_red_min_score)
    if os.environ.get("BREAKOUT_RED_MIN_VOL_RATIO"):
        config.breakout_red_min_vol_ratio = env_float("BREAKOUT_RED_MIN_VOL_RATIO", config.breakout_red_min_vol_ratio)
    if os.environ.get("BREAKOUT_RED_WATCHLIST_SIZE"):
        config.breakout_red_watchlist_size = env_int("BREAKOUT_RED_WATCHLIST_SIZE", config.breakout_red_watchlist_size)


def _validate(config: "Config"):
    errors = []
    if config.enable_solana and "YOUR_HELIUS" in (config.solana_rpc_url or ""):
        errors.append(
            "SOLANA_RPC_URL missing — set SOLANA_RPC_URL in Railway Variables"
        )
    if config.avg_down_max_loss_pct > config.stop_loss_pct:
        errors.append(
            f"avg_down_max_loss ({config.avg_down_max_loss_pct}%) must be "
            f"less than stop_loss ({config.stop_loss_pct}%)"
        )
    if not config.birdeye_api_key:
        print("Warning: BIRDEYE_API_KEY not set — DexScreener only")
    if errors:
        print("\nCONFIG ERRORS:")
        for e in errors:
            print(f"  - {e}")
        raise SystemExit("Fix config errors before running.")
