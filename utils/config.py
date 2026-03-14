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
from typing import List, Dict


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
    evm_private_key: str = ""
    scalper_solana_private_key: str = ""
    scalper_evm_private_key: str = ""

    # ── RPC Endpoints ────────────────────────────────────────
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    base_rpc_url: str = "https://mainnet.base.org"
    bnb_rpc_url: str = "https://bsc-dataseed1.binance.org"

    # ── API Keys ─────────────────────────────────────────────
    basescan_api_key: str = ""
    bscscan_api_key: str = ""
    birdeye_api_key: str = ""

    # ── Telegram ─────────────────────────────────────────────
    telegram_token: str = ""
    telegram_chat_id: str = ""
    dashboard_port: int = 8080

    # ── Chains ───────────────────────────────────────────────
    enable_solana: bool = True
    enable_base: bool = True
    enable_bnb: bool = True

    # ── Capital ──────────────────────────────────────────────
    total_capital: float = 2000.0
    max_position_pct: float = 0.08
    min_position_pct: float = 0.02
    daily_loss_limit: float = 200.0
    capital_split: Dict = field(default_factory=lambda: {
        "solana": 0.50, "base": 0.30, "bnb": 0.20
    })

    # ── Take Profit ──────────────────────────────────────────
    take_profit_1_pct: float = 50.0
    take_profit_1_sell: float = 0.50
    take_profit_2_pct: float = 100.0
    take_profit_2_sell: float = 0.75
    take_profit_3_pct: float = 150.0
    take_profit_3_sell: float = 0.75

    # ── Stop Loss ────────────────────────────────────────────
    stop_loss_pct: float = 28.0

    # ── Stall Detection ──────────────────────────────────────
    stall_check_interval_min: int = 30
    stall_volume_threshold: float = 0.20
    stall_min_hours: float = 1.0
    stall_sell_pct: float = 0.75

    # ── Average Down ─────────────────────────────────────────
    avg_down_max_loss_pct: float = 15.0
    avg_down_min_volume_pct: float = 0.50
    avg_down_size_pct: float = 0.50

    # ── Market Conditions ────────────────────────────────────
    btc_drop_threshold: float = 5.0
    restricted_score_threshold: int = 85
    override_score: int = 90

    # ── Scanner ──────────────────────────────────────────────
    min_mcap: float = 200_000
    max_mcap: float = 1_000_000
    min_combined_score: int = 65
    require_both_sources: bool = True
    min_liquidity_usd: float = 50_000
    max_dev_wallet_pct: float = 5.0
    preferred_age_min_hours: float = 3.0
    preferred_age_max_hours: float = 12.0
    hard_skip_age_hours: float = 24.0
    pyramid_score_threshold: int = 90
    volume_acceleration_candles: int = 3

    # ── Security ─────────────────────────────────────────────
    max_buy_tax: float = 10.0
    max_sell_tax: float = 10.0
    max_top10_concentration: float = 80.0
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
    base_copy_wallets: List[str] = field(default_factory=list)
    bnb_copy_wallets: List[str] = field(default_factory=list)
    copy_trade_delay_seconds: int = 5
    copy_min_trade_size_native: float = 0.01
    copy_max_price_move_pct: float = 15.0
    copy_min_hold_hours: float = 1.0
    copy_max_hold_hours: float = 4.0
    copy_min_win_rate: float = 0.50
    copy_min_range_concentration: float = 0.50

    # ── Scalper ──────────────────────────────────────────────
    scalper_sell_trigger_pct: float = 15.0
    scalper_rebuy_trigger_pct: float = 20.0
    scalper_sell_pct: float = 0.25
    scalper_max_cycles: int = 4
    scalper_rebuy_window_hours: float = 2.0
    scalper_min_profit_usd: float = 5.0
    scalper_require_recovery: bool = True

    # ── Analytics ────────────────────────────────────────────
    kelly_fraction: float = 0.50
    target_win_rate: float = 0.55
    min_sentiment_score: int = 30
    require_twitter: bool = True
    use_flashbots: bool = True
    large_buy_threshold_sol: float = 5.0

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
      EVM_PRIVATE_KEY
      TELEGRAM_TOKEN
      TELEGRAM_CHAT_ID
      SOLANA_RPC_URL
      BASESCAN_API_KEY
      BSCSCAN_API_KEY
      BIRDEYE_API_KEY
      (and any others you want to override)
    """
    # Wallet keys — most sensitive, always from env on Railway
    if os.environ.get("SOLANA_PRIVATE_KEY"):
        config.solana_private_key = env("SOLANA_PRIVATE_KEY")
    if os.environ.get("EVM_PRIVATE_KEY"):
        config.evm_private_key = env("EVM_PRIVATE_KEY")
    if os.environ.get("SCALPER_SOLANA_PRIVATE_KEY"):
        config.scalper_solana_private_key = env("SCALPER_SOLANA_PRIVATE_KEY")
    if os.environ.get("SCALPER_EVM_PRIVATE_KEY"):
        config.scalper_evm_private_key = env("SCALPER_EVM_PRIVATE_KEY")

    # RPC URLs
    if os.environ.get("SOLANA_RPC_URL"):
        config.solana_rpc_url = env("SOLANA_RPC_URL")
    if os.environ.get("BASE_RPC_URL"):
        config.base_rpc_url = env("BASE_RPC_URL")
    if os.environ.get("BNB_RPC_URL"):
        config.bnb_rpc_url = env("BNB_RPC_URL")

    # API keys
    if os.environ.get("BASESCAN_API_KEY"):
        config.basescan_api_key = env("BASESCAN_API_KEY")
    if os.environ.get("BSCSCAN_API_KEY"):
        config.bscscan_api_key = env("BSCSCAN_API_KEY")
    if os.environ.get("BIRDEYE_API_KEY"):
        config.birdeye_api_key = env("BIRDEYE_API_KEY")

    # Telegram
    if os.environ.get("TELEGRAM_TOKEN"):
        config.telegram_token = env("TELEGRAM_TOKEN")
    if os.environ.get("TELEGRAM_CHAT_ID"):
        config.telegram_chat_id = env("TELEGRAM_CHAT_ID")

    # Capital settings (optional overrides)
    if os.environ.get("TOTAL_CAPITAL"):
        config.total_capital = env_float("TOTAL_CAPITAL", config.total_capital)
    if os.environ.get("DAILY_LOSS_LIMIT"):
        config.daily_loss_limit = env_float("DAILY_LOSS_LIMIT", config.daily_loss_limit)

    # Chain toggles
    if os.environ.get("ENABLE_SOLANA"):
        config.enable_solana = env_bool("ENABLE_SOLANA", config.enable_solana)
    if os.environ.get("ENABLE_BASE"):
        config.enable_base = env_bool("ENABLE_BASE", config.enable_base)
    if os.environ.get("ENABLE_BNB"):
        config.enable_bnb = env_bool("ENABLE_BNB", config.enable_bnb)

    # Copy wallets (comma-separated in env)
    if os.environ.get("SOLANA_COPY_WALLETS"):
        config.solana_copy_wallets = env_list("SOLANA_COPY_WALLETS")
    if os.environ.get("BASE_COPY_WALLETS"):
        config.base_copy_wallets = env_list("BASE_COPY_WALLETS")
    if os.environ.get("BNB_COPY_WALLETS"):
        config.bnb_copy_wallets = env_list("BNB_COPY_WALLETS")

    # Dashboard port (Railway assigns this automatically)
    if os.environ.get("PORT"):
        config.dashboard_port = env_int("PORT", config.dashboard_port)


def _validate(config: "Config"):
    errors = []
    if config.enable_solana and "YOUR_HELIUS" in (config.solana_rpc_url or ""):
        errors.append(
            "SOLANA_RPC_URL missing — set SOLANA_RPC_URL in Railway Variables"
        )
    if abs(sum(config.capital_split.values()) - 1.0) > 0.01:
        errors.append("capital_split must add to 1.0")
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
