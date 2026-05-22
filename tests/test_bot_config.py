import pytest
from dataclasses import FrozenInstanceError
from core.bot_config import BotConfig

def test_botconfig_required_fields():
    cfg = BotConfig(bot_id="b1", display_name="Bot 1")
    assert cfg.bot_id == "b1"
    assert cfg.display_name == "Bot 1"
    assert cfg.enabled is True
    assert cfg.paper_capital_usd == 2000.0
    assert cfg.base_position_usd == 20.0
    assert cfg.max_concurrent_positions == 3

def test_botconfig_is_frozen():
    cfg = BotConfig(bot_id="b1", display_name="Bot 1")
    with pytest.raises(FrozenInstanceError):
        cfg.bot_id = "b2"

def test_botconfig_defaults_match_production():
    cfg = BotConfig(bot_id="baseline_v1", display_name="Baseline")
    # SOL gate matches commit 9fe8366
    assert cfg.sol_macro_h6_block_threshold == -0.3
    assert cfg.sol_macro_h1_block_threshold == -0.7
    # pc_h24 gate matches commit 9840ffe (mcap_psych_level)
    assert cfg.mcap_psych_pc_h24_max == 80.0
    # Exit ladder matches current production
    assert cfg.tp1_pct == 5.0
    assert cfg.tp1_sell_fraction == 0.75
    assert cfg.hard_stop_pct == -15.0

def test_botconfig_rejects_both_filters_enforced_and_disabled():
    with pytest.raises(ValueError, match="filters_disabled must be empty"):
        BotConfig(
            bot_id="bad",
            display_name="Bad",
            filters_enforced=("filter_corpse",),
            filters_disabled=("filter_fake_bounce",),
        )

def test_botconfig_rejects_both_triggers_allowed_and_disabled():
    with pytest.raises(ValueError, match="triggers_disabled must be empty"):
        BotConfig(
            bot_id="bad",
            display_name="Bad",
            triggers_allowed=("deep_1h_dip",),
            triggers_disabled=("mcap_psych_level",),
        )

def test_botconfig_rejects_tp_sell_fractions_over_one():
    with pytest.raises(ValueError, match="tp1_sell_fraction"):
        BotConfig(
            bot_id="bad",
            display_name="Bad",
            tp1_sell_fraction=0.8,
            tp2_sell_fraction=0.5,
        )

def test_botconfig_allows_tp_sell_fractions_summing_to_one():
    # 0.75 + 0.25 = 1.0 should pass (production default)
    cfg = BotConfig(bot_id="ok", display_name="OK")
    assert cfg.tp1_sell_fraction + cfg.tp2_sell_fraction == 1.0
