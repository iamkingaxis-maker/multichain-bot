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
    total_capital: float = 2000.0
    max_position_pct: float = 0.08
    max_position_usd: float = 200.0
    min_position_pct: float = 0.02
    daily_loss_limit: float = 200.0

    # ── Take Profit ──────────────────────────────────────────
    take_profit_1_pct: float = 10.0   # +10% → sell 100% (clean exit, lock the gain)
    take_profit_1_sell: float = 1.0
    take_profit_2_pct: float = 75.0
    take_profit_2_sell: float = 0.40
    take_profit_3_pct: float = 150.0
    take_profit_3_sell: float = 1.0

    # ── Micro-Cap Take Profit (separate tiers for fresh launches) ──────
    mc_tp1_pct: float = 10.0        # +10% → sell 100% (clean exit, lock the gain)
    mc_tp1_sell: float = 1.0
    mc_tp2_pct: float = 75.0        # +75% → sell 40% of remaining
    mc_tp2_sell: float = 0.40
    mc_tp3_pct: float = 200.0       # +200% → sell everything
    mc_tp3_sell: float = 1.0
    mc_stop_loss_pct: float = 25.0  # Wider — PumpSwap graduates routinely dip 20-30% on normal post-grad volatility
    mc_winner_trail_pct: float = 15.0  # Trail 15% from peak (more room for volatility)

    # ── Stop Loss ────────────────────────────────────────────
    stop_loss_pct: float = 7.0

    # ── Winner Protection ────────────────────────────────────
    winner_trail_pct: float = 15.0  # Close 100% if drops 15% from peak after TP1

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

    # ── Scanner ──────────────────────────────────────────────
    min_mcap: float = 80_000
    max_mcap: float = 1_000_000
    max_volume_h1_usd: float = 300_000

    # ── Micro-Cap Mode (AxiomScanner only) ───────────────────
    # Targets fresh $10k-$50k pairs via Axiom WS with tighter gates
    micro_cap_enabled: bool = True
    micro_cap_min_mcap: float = 10_000
    micro_cap_max_mcap: float = 80_000  # Raised from 50k — covers PumpSwap graduates (~$67k mcap)
    micro_cap_position_usd: float = 40.0
    micro_cap_max_snipers_pct: float = 15.0   # block if snipers hold > 15%
    micro_cap_max_dev_pct: float = 10.0        # block if dev holds > 10%
    min_volume_h1_usd: float = 8_000
    min_combined_score: int = 0
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
