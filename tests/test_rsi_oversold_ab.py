"""rsi_oversold_ab A/B (2026-06-28): a PAPER-only clone of badday_flush plus ONE
added entry requirement — entry_rsi_15m_max=44 (15-min RSI oversold). Verifies:

  1. the new per-bot gate PASSES rsi_15m<=44 and BLOCKS rsi_15m>44,
  2. missing/None rsi_15m is FAIL-CLOSED (token skipped) — the documented choice,
  3. env RSI_OVERSOLD_MAX overrides the per-bot cap,
  4. existing bots (badday_flush control + baseline_v1) are byte-identical wrt the
     new config key (default None -> gate never runs),
  5. the new config parses + registers cleanly and is a true badday_flush twin
     (badday_ prefix for the lane mandate + family gates; only the rsi gate differs).
"""
from pathlib import Path
import pytest
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle
from core.bot_evaluator import BotEvaluator
from core.bot_registry import BotRegistry

_BOTS_DIR = Path(__file__).parent.parent / "config" / "bots"


def _bundle(**overrides):
    defaults = dict(
        token="TEST", address="a", pair_address="p", chain="solana",
        snapshot_ts=1716480000.0, price_usd=0.001, mcap_usd=4_000_000.0,
        age_hours=240.0,
        pc_h24=None, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=50_000.0, bs_h1=None,
        sol_pc_h1=None, sol_pc_h4=None, sol_pc_h6=None, sol_pc_h24=None,
        btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,
        net_flow_15s_usd=None, net_flow_60s_usd=None, net_flow_5m_usd=None,
        top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None,
        cnn_cluster_id=None, fusion_outcome_prob=None,
        triggers_fired=("vol_breakout",),
        triggers_shadow=(), filters_block=(), filters_pass=(), filters_shadow=(),
        raw_meta={},
    )
    defaults.update(overrides)
    return FeatureBundle(**defaults)


def _cfg(**overrides):
    base = dict(bot_id="b1", display_name="Bot 1")
    base.update(overrides)
    return BotConfig(**base)


# ---------------------------------------------------------------------------
# 1. the gate itself: <=44 passes, >44 blocks
# ---------------------------------------------------------------------------
def test_rsi_gate_passes_oversold_and_blocks_hot():
    ev = BotEvaluator(_cfg(entry_rsi_15m_max=44.0))
    # rsi 40 <= 44 -> PASS
    assert ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": 40.0})) is True
    # boundary: exactly 44 -> PASS (<=)
    assert ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": 44.0})) is True
    # rsi 55 > 44 -> BLOCK
    assert ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": 55.0})) is False
    # just over the line
    assert ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": 44.01})) is False


# ---------------------------------------------------------------------------
# 2. missing/None rsi -> FAIL-CLOSED (documented choice)
# ---------------------------------------------------------------------------
def test_rsi_gate_missing_feature_is_fail_closed():
    ev = BotEvaluator(_cfg(entry_rsi_15m_max=44.0))
    # key absent -> blocked
    assert ev._token_regime_passes(_bundle(raw_meta={})) is False
    # explicit None -> blocked
    assert ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": None})) is False
    # non-numeric -> blocked
    assert ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": "n/a"})) is False
    # bool must NOT be treated as a number -> blocked
    assert ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": True})) is False


# ---------------------------------------------------------------------------
# 3. env RSI_OVERSOLD_MAX overrides the per-bot cap
# ---------------------------------------------------------------------------
def test_env_overrides_threshold(monkeypatch):
    ev = BotEvaluator(_cfg(entry_rsi_15m_max=44.0))
    # tighten via env to 30: an rsi of 40 now BLOCKS (was passing at cap 44)
    monkeypatch.setenv("RSI_OVERSOLD_MAX", "30")
    assert ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": 40.0})) is False
    assert ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": 29.0})) is True
    # garbage env -> falls back to the per-bot cap (44), 40 passes
    monkeypatch.setenv("RSI_OVERSOLD_MAX", "not-a-number")
    assert ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": 40.0})) is True


# ---------------------------------------------------------------------------
# 4. existing bots are byte-identical wrt the new key (gate disabled by default)
# ---------------------------------------------------------------------------
def test_default_off_does_not_gate_existing_bots(monkeypatch):
    # A config without the key -> entry_rsi_15m_max defaults to None -> the rsi gate
    # branch is never entered, so the decision is identical regardless of rsi_15m
    # (and even regardless of the env var being set).
    monkeypatch.setenv("RSI_OVERSOLD_MAX", "10")  # would block everything IF it ran
    ev = BotEvaluator(_cfg())  # no entry_rsi_15m_max
    assert ev.config.entry_rsi_15m_max is None
    hot = ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": 99.0}))
    cold = ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": 5.0}))
    missing = ev._token_regime_passes(_bundle(raw_meta={}))
    assert hot == cold == missing  # rsi never affects a non-opted-in bot


def test_real_control_bots_unaffected_by_new_key(monkeypatch):
    # The actual on-disk control (badday_flush) and an unrelated bot (baseline_v1)
    # must not carry the gate and must decide identically regardless of rsi_15m.
    monkeypatch.setenv("RSI_OVERSOLD_MAX", "10")
    reg = BotRegistry.from_directory(_BOTS_DIR)
    by_id = {c.bot_id: c for c in reg.configs}
    for bid in ("badday_flush", "baseline_v1"):
        cfg = by_id[bid]
        assert cfg.entry_rsi_15m_max is None, f"{bid} unexpectedly carries the rsi gate"
        ev = BotEvaluator(cfg)
        d_hot = ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": 99.0}))
        d_cold = ev._token_regime_passes(_bundle(raw_meta={"rsi_15m": 5.0}))
        assert d_hot == d_cold, f"{bid} decision changed with rsi_15m -> not byte-identical"


# ---------------------------------------------------------------------------
# 5. the new config parses, registers, and is a true badday_flush twin
# ---------------------------------------------------------------------------
def test_rsi_ab_config_loads_and_registers():
    reg = BotRegistry.from_directory(_BOTS_DIR)
    by_id = {c.bot_id: c for c in reg.configs}
    assert "badday_flush_rsi_ab" in by_id, "rsi A/B clone did not register"
    ab = by_id["badday_flush_rsi_ab"]
    # the single added variable
    assert ab.entry_rsi_15m_max == 44.0
    # paper-only + enabled
    assert ab.enabled is True
    # badday_ prefix is load-bearing (lane mandate + family entry/exit gates)
    assert ab.bot_id.startswith("badday_")


def test_rsi_ab_is_clean_clone_of_badday_flush():
    reg = BotRegistry.from_directory(_BOTS_DIR)
    by_id = {c.bot_id: c for c in reg.configs}
    base = by_id["badday_flush"]
    ab = by_id["badday_flush_rsi_ab"]
    # everything that defines the strategy/sizing/exits matches the control;
    # the ONLY intended differences are bot_id, display_name, entry_rsi_15m_max.
    for field in ("entry_gate", "filters_enforced", "mcap_min", "mcap_max",
                  "age_h_min", "hard_stop_pct", "tp1_pct", "tp2_pct",
                  "trail_pp", "base_position_usd", "paper_capital_usd",
                  "max_concurrent_positions", "daily_loss_limit_usd",
                  "never_runner_loss_floor", "giveback_floor_pnl_pct"):
        assert getattr(ab, field) == getattr(base, field), (
            f"clone diverges from badday_flush on {field}: "
            f"{getattr(ab, field)!r} != {getattr(base, field)!r}"
        )
    # control must NOT carry the gate (it's the A/B baseline)
    assert base.entry_rsi_15m_max is None
