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
