"""Verify the catalog of 24 bots: each loads, each differs from baseline
by exactly the expected fields, and there are no duplicate bot_ids."""
import pytest
from pathlib import Path
from core.bot_registry import BotRegistry
from core.bot_config import BotConfig


@pytest.fixture(scope="module")
def catalog():
    return BotRegistry.from_directory(Path(__file__).parent.parent / "config" / "bots")


@pytest.fixture(scope="module")
def baseline(catalog):
    by_id = {c.bot_id: c for c in catalog.configs}
    return by_id["baseline_v1"]


def _by_id(catalog):
    return {c.bot_id: c for c in catalog.configs}


def test_catalog_has_24_bots(catalog):
    assert len(catalog.configs) == 24, (
        f"Expected 24 bots, got {len(catalog.configs)}: "
        f"{[c.bot_id for c in catalog.configs]}"
    )


def test_catalog_no_duplicate_ids(catalog):
    ids = [c.bot_id for c in catalog.configs]
    assert len(ids) == len(set(ids)), f"Duplicate ids: {ids}"


def test_baseline_present(baseline):
    assert baseline.bot_id == "baseline_v1"


# Single-knob ablations
def test_no_sol_gate_diff(catalog, baseline):
    bot = _by_id(catalog)["no_sol_gate"]
    assert bot.sol_macro_h6_block_threshold is None
    assert bot.sol_macro_h1_block_threshold is None
    assert bot.mcap_psych_pc_h24_max == baseline.mcap_psych_pc_h24_max


def test_no_filters_diff(catalog, baseline):
    bot = _by_id(catalog)["no_filters"]
    assert bot.filters_enforced == ()
    assert bot.sol_macro_h6_block_threshold == baseline.sol_macro_h6_block_threshold


def test_no_alpha_sizing_diff(catalog, baseline):
    bot = _by_id(catalog)["no_alpha_sizing"]
    assert bot.alpha_multiplier == 1.0
    assert baseline.alpha_multiplier == 1.5


def test_no_pc_h24_ceiling_diff(catalog, baseline):
    bot = _by_id(catalog)["no_pc_h24_ceiling"]
    assert bot.mcap_psych_pc_h24_max is None
    assert baseline.mcap_psych_pc_h24_max == 80.0


def test_wide_concurrent_diff(catalog, baseline):
    bot = _by_id(catalog)["wide_concurrent"]
    assert bot.max_concurrent_positions == 5
    assert baseline.max_concurrent_positions == 3


def test_narrow_concurrent_diff(catalog, baseline):
    bot = _by_id(catalog)["narrow_concurrent"]
    assert bot.max_concurrent_positions == 1


def test_tight_stop_diff(catalog, baseline):
    bot = _by_id(catalog)["tight_stop"]
    assert bot.hard_stop_pct == -10.0
    assert baseline.hard_stop_pct == -15.0


def test_wide_stop_diff(catalog, baseline):
    bot = _by_id(catalog)["wide_stop"]
    assert bot.hard_stop_pct == -20.0


# Thesis bots
def test_strict_alpha_only_diff(catalog, baseline):
    bot = _by_id(catalog)["strict_alpha_only"]
    assert bot.require_alpha_trigger is True
    assert baseline.require_alpha_trigger is False


def test_runner_tilt_aggressive_diff(catalog, baseline):
    bot = _by_id(catalog)["runner_tilt_aggressive"]
    assert bot.tp1_pct == 8.0
    assert bot.tp1_sell_fraction == 0.33
    assert bot.tp2_pct == 20.0
    assert bot.tp2_sell_fraction == 0.33
    assert bot.trail_pp == 4.0


def test_scalp_only_diff(catalog, baseline):
    bot = _by_id(catalog)["scalp_only"]
    assert bot.tp1_pct == 3.0
    assert bot.tp1_sell_fraction == 1.0
    assert bot.tp2_pct == 999.0
    assert bot.tp2_sell_fraction == 0.0


def test_regime_aware_bullish_diff(catalog, baseline):
    bot = _by_id(catalog)["regime_aware_bullish"]
    assert bot.sol_macro_h1_block_threshold == 0.0
    assert bot.btc_macro_h1_block_threshold == 0.0


def test_microcap_specialist_diff(catalog, baseline):
    bot = _by_id(catalog)["microcap_specialist"]
    assert bot.mcap_min == 500_000.0
    assert bot.mcap_max == 3_000_000.0


def test_midcap_specialist_diff(catalog, baseline):
    bot = _by_id(catalog)["midcap_specialist"]
    assert bot.mcap_min == 5_000_000.0
    assert bot.mcap_max == 25_000_000.0


def test_early_token_only_diff(catalog, baseline):
    bot = _by_id(catalog)["early_token_only"]
    assert bot.age_h_max == 24.0


def test_mature_token_only_diff(catalog, baseline):
    bot = _by_id(catalog)["mature_token_only"]
    assert bot.age_h_min == 168.0


# Trigger-set isolation bots
def test_whales_only_uses_allowlist(catalog):
    bot = _by_id(catalog)["whales_only"]
    assert bot.triggers_allowed is not None
    assert "whale_concentrated_demand" in bot.triggers_allowed
    assert "whale_recent_burst" in bot.triggers_allowed
    assert "concurrent_alpha" in bot.triggers_allowed


def test_chart_pattern_only_uses_allowlist(catalog):
    bot = _by_id(catalog)["chart_pattern_only"]
    assert bot.triggers_allowed is not None
    assert "chart_quality_bottom" in bot.triggers_allowed
    assert "chart_channel_strong" in bot.triggers_allowed
    assert "mtf_aligned_demand" in bot.triggers_allowed


def test_one_sec_only_uses_allowlist(catalog):
    bot = _by_id(catalog)["one_sec_only"]
    assert bot.triggers_allowed == (
        "1s_capit_reversal", "1s_demand_compound", "1s_v_bottom_strict",
    )


def test_flow_only_uses_allowlist(catalog):
    bot = _by_id(catalog)["flow_only"]
    assert bot.triggers_allowed is not None
    assert "bullish_engulfing_5m" in bot.triggers_allowed
    assert "net_flow_5m_demand" in bot.triggers_allowed
    assert "demand_burst_no_crash" in bot.triggers_allowed


def test_deep_dip_only_uses_allowlist(catalog):
    bot = _by_id(catalog)["deep_dip_only"]
    assert bot.triggers_allowed is not None
    assert "deep_1h_dip" in bot.triggers_allowed
    assert "sweep_rejection" in bot.triggers_allowed


def test_cnn_cluster_only_uses_allowlist(catalog):
    bot = _by_id(catalog)["cnn_cluster_only"]
    assert bot.triggers_allowed == (
        "cnn_cluster_10", "cnn_cluster_13", "cnn_cluster_16",
    )


def test_isolation_bots_have_no_filters_disabled(catalog):
    """All 6 isolation bots should inherit baseline filter behavior."""
    for bid in ["whales_only", "chart_pattern_only", "one_sec_only",
                "flow_only", "deep_dip_only", "cnn_cluster_only"]:
        bot = _by_id(catalog)[bid]
        assert bot.filters_enforced is None, f"{bid} should leave filters_enforced=None"
        assert bot.filters_disabled == (), f"{bid} should leave filters_disabled empty"


def test_champion_proposal_disabled(catalog):
    bot = _by_id(catalog)["champion_proposal"]
    assert bot.enabled is False


def test_all_paper_capital_2000(catalog):
    """All bots get $2000 paper capital — keeps comparison fair."""
    for c in catalog.configs:
        assert c.paper_capital_usd == 2000.0, (
            f"{c.bot_id} has paper_capital={c.paper_capital_usd}, expected 2000"
        )


def test_all_base_position_20(catalog):
    """All bots use $20 base position."""
    for c in catalog.configs:
        assert c.base_position_usd == 20.0
