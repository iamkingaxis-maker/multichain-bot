"""Verify the catalog of 103 bots: each loads, each differs from baseline
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


def test_catalog_has_120_bots(catalog):
    # 2026-05-27: +3 eq_*_pullback + 4 eqc_pullback_* compound-gate test bots;
    # 14 dead/dup bots disabled (still on disk, so file count holds).
    # 2026-05-28: +1 mom_continuation_v1_test (overnight E1' mine ship).
    # 2026-05-28 PM: +4 layered defender bots (perf-diff mine):
    #   champion_defender_falling_pump (G1 single-feature test)
    #   champion_defender_fusion (fusion floor single-feature test)
    #   champion_defender_btc (BTC overheat single-feature test)
    #   champion_defender_v3 (full 6-filter layered, production-successor candidate)
    # 2026-05-29: +1 champion_defender_2k (7h-watch rec #1: 8-filter defender +
    #   stall-exit on the cap2k_turnover $2k spine; cap2k_turnover stays control).
    # 2026-05-29: +1 champion_defender_volaccel (single-filter isolation probe
    #   for filter_dead_volume).
    assert len(catalog.configs) == 122, (
        f"Expected 122 bots, got {len(catalog.configs)}: "
        f"{[c.bot_id for c in catalog.configs]}"
    )


def test_layered_defender_bots_present(catalog):
    """2026-05-28 PM perf-diff mine: 4 defender bots opt in via filters_enforced
    to the new DEFENDER_FILTERS set in core/bot_evaluator.py.
    Held-out 4x lift out-of-sample on 1487 paired trades from 27-28 window."""
    by_id = _by_id(catalog)
    for bid in ["champion_defender_falling_pump", "champion_defender_fusion",
                "champion_defender_btc", "champion_defender_v3",
                "champion_defender_volaccel"]:
        assert bid in by_id, f"Missing defender bot: {bid}"

    # fusion stays a single-feature isolation probe.
    assert by_id["champion_defender_fusion"].filters_enforced == ("filter_fusion_floor",)
    # champion_defender_volaccel: single-filter isolation probe for the new
    # held-out entry gate (2026-05-29).
    assert by_id["champion_defender_volaccel"].filters_enforced == ("filter_dead_volume",)
    # btc + falling_pump ALSO carry filter_dead_volume (2026-05-29) — their own
    # filters fire rarely (near-baseline), so they double as vol-accel probes for
    # faster data. No longer strictly single-filter.
    assert set(by_id["champion_defender_falling_pump"].filters_enforced) == {
        "filter_falling_pump", "filter_dead_volume"}
    assert set(by_id["champion_defender_btc"].filters_enforced) == {
        "filter_btc_overheat", "filter_dead_volume"}

    # v3 opts in to all 9 defender filters (filter_dead_meme_lagging_pressure +
    # filter_dead_low_demand added 2026-05-29; filter_dead_volume added 2026-05-29
    # — held-out entry-quality gate, defended WR 51%->70%, defender-scoped)
    v3 = by_id["champion_defender_v3"]
    assert v3.filters_enforced is not None
    assert set(v3.filters_enforced) == {
        "filter_falling_pump", "filter_fusion_floor", "filter_btc_overheat",
        "filter_aged_corpse", "filter_wynn_killer", "filter_consec_red",
        "filter_dead_meme_lagging_pressure", "filter_dead_low_demand",
        "filter_dead_volume",
    }

    # champion_defender_2k (7h-watch rec #1): 8-filter defender on cap2k $2k spine
    d2k = by_id["champion_defender_2k"]
    assert d2k.filters_enforced is not None
    assert set(d2k.filters_enforced) == set(v3.filters_enforced)
    assert d2k.base_position_usd == 650.0
    assert d2k.stall_exit_minutes == 90


def test_volume_experiment_bots_present(catalog):
    """2026-05-25 volume/capital stack (P-stack #1-4): capital-absorption
    ladder, conviction sizing, velocity exit, reentry throttle."""
    ids = {c.bot_id for c in catalog.configs}
    assert {"champ_size_2x", "champ_size_4x", "champ_size_8x",
            "champ_conviction", "champ_velocity", "champ_reentry_throttle"} <= ids


def test_conviction_bot_config(catalog):
    bot = _by_id(catalog)["champ_conviction"]
    assert bot.conviction_sizing_mode == "trigger_count"


def test_velocity_bot_config(catalog):
    bot = _by_id(catalog)["champ_velocity"]
    assert bot.flat_exit_minutes == 45


def test_reentry_throttle_bot_config(catalog):
    bot = _by_id(catalog)["champ_reentry_throttle"]
    assert bot.reentry_cooldown_secs == 3600.0


def test_champion_bracket_present(catalog):
    """2026-05-25 champion tournament: 5 synthesized champions + baseline_v1
    control. Each built from winning knobs in the combined (realized+unrealized)
    dimensional sweep. See reference_champion_bracket_2026_05_25."""
    ids = {c.bot_id for c in catalog.configs}
    assert {"champ_sniper", "champ_workhorse", "champ_regime_rider",
            "champ_specialist", "champ_runner"} <= ids


def test_champion_runner_exit_ladder(catalog):
    bot = _by_id(catalog)["champ_runner"]
    assert bot.tp1_pct == 15.0 and bot.tp1_sell_fraction == 0.25
    assert bot.tp2_pct == 50.0 and bot.tp2_sell_fraction == 0.5


def test_champion_sniper_concentration(catalog):
    bot = _by_id(catalog)["champ_sniper"]
    assert bot.max_concurrent_positions == 2 and bot.base_position_usd == 40.0
    assert bot.triggers_allowed is not None  # 1s triggers only


def test_deploy_c_bots_present(catalog):
    """Deploy C 2026-05-23: code-required bots. reentry_after_stop deferred."""
    ids = {c.bot_id for c in catalog.configs}
    assert {"drawdown_freeze", "macro_conditional"} <= ids


def test_drawdown_freeze_config(catalog):
    bot = _by_id(catalog)["drawdown_freeze"]
    assert bot.drawdown_freeze_threshold_usd == -100.0


def test_macro_conditional_config(catalog):
    bot = _by_id(catalog)["macro_conditional"]
    assert bot.macro_conditional_mode == "sol_h6"
    # Binary sol_macro disabled so gradient sizing isn't pre-empted
    assert bot.sol_macro_h6_block_threshold is None
    assert bot.sol_macro_h1_block_threshold is None


def test_deploy_b_bots_present(catalog):
    """Deploy B 2026-05-23: capital concentration + stop bounds + slow-bleed sensitivity."""
    ids = {c.bot_id for c in catalog.configs}
    assert {"concentrated_50", "spray_10",
            "stop_8", "stop_25",
            "bleed_30min", "bleed_120min", "no_bleed"} <= ids


def test_concentrated_50_config(catalog):
    bot = _by_id(catalog)["concentrated_50"]
    assert bot.base_position_usd == 50.0
    assert bot.max_concurrent_positions == 1


def test_spray_10_config(catalog):
    bot = _by_id(catalog)["spray_10"]
    assert bot.base_position_usd == 10.0
    assert bot.max_concurrent_positions == 6


def test_stop_8_config(catalog):
    assert _by_id(catalog)["stop_8"].hard_stop_pct == -8.0


def test_stop_25_config(catalog):
    assert _by_id(catalog)["stop_25"].hard_stop_pct == -25.0


def test_bleed_variants_config(catalog):
    by = _by_id(catalog)
    assert by["bleed_30min"].slow_bleed_minutes == 30
    assert by["bleed_120min"].slow_bleed_minutes == 120
    assert by["no_bleed"].slow_bleed_minutes == 99999


def test_tp_ladder_bots_present(catalog):
    """TP ladder variants shipped 2026-05-23 to test exit-aggression."""
    ids = {c.bot_id for c in catalog.configs}
    assert {"tp_aggressive", "tp_runner", "tp_single_target"} <= ids


def test_tp_aggressive_config(catalog):
    bot = _by_id(catalog)["tp_aggressive"]
    assert bot.tp1_pct == 3.0
    assert bot.tp2_pct == 7.0


def test_tp_runner_config(catalog):
    bot = _by_id(catalog)["tp_runner"]
    assert bot.tp1_pct == 8.0
    assert bot.tp2_pct == 20.0


def test_tp_single_target_config(catalog):
    bot = _by_id(catalog)["tp_single_target"]
    assert bot.tp1_pct == 5.0
    assert bot.tp1_sell_fraction == 1.0
    assert bot.tp2_sell_fraction == 0.0


def test_compound_bots_present(catalog):
    """Three compounding bots added 2026-05-23 to test the
    profit-scales-position-size hypothesis: linear (symmetric grow/shrink),
    winners_only (asymmetric — only grow), threshold (discrete steps)."""
    ids = {c.bot_id for c in catalog.configs}
    assert {"compound_linear", "compound_winners_only", "compound_threshold"} <= ids


def test_compound_linear_config(catalog):
    bot = _by_id(catalog)["compound_linear"]
    assert bot.compound_mode == "linear"
    assert bot.compound_max_multiplier == 5.0


def test_compound_winners_only_config(catalog):
    bot = _by_id(catalog)["compound_winners_only"]
    assert bot.compound_mode == "winners_only"


def test_compound_threshold_config(catalog):
    bot = _by_id(catalog)["compound_threshold"]
    assert bot.compound_mode == "threshold"
    assert bot.compound_threshold_step_usd == 100.0
    assert bot.compound_step_amount_usd == 5.0


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
    # Raised 24h -> 72h (2026-05-23): 24h was structurally inert because the
    # production trigger set never fires on <24h tokens. 72h is still 27x
    # fresher than current buy median.
    assert bot.age_h_max == 72.0


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


def test_champion_proposal_is_enabled_synthesis(catalog):
    """2026-05-25: champion_proposal populated as the integrated production
    candidate (proven knobs only) + ENABLED to compete from the fresh
    baseline. Specialist universe + scale-out partial ladder (P1) + no SOL
    gate + vol floor. The SP5 cutover target. Conviction/velocity/reentry
    deliberately excluded until their solo bots validate (held-out discipline)."""
    bot = _by_id(catalog)["champion_proposal"]
    assert bot.enabled is True
    assert bot.mcap_min == 500000.0 and bot.mcap_max == 25000000.0
    assert bot.tp1_sell_fraction == 0.5 and bot.tp2_sell_fraction == 0.25
    assert bot.sol_macro_h6_block_threshold is None
    assert bot.conviction_sizing_mode is None  # unproven — held for v2


def test_all_paper_capital_2000(catalog):
    """All bots get $2000 paper capital — keeps comparison fair."""
    for c in catalog.configs:
        assert c.paper_capital_usd == 2000.0, (
            f"{c.bot_id} has paper_capital={c.paper_capital_usd}, expected 2000"
        )


def test_all_base_position_20(catalog):
    """All bots use $20 base position EXCEPT the capital-concentration
    variants shipped 2026-05-23 which explicitly test that dimension."""
    EXEMPT = {"concentrated_50", "spray_10", "champ_sniper",
              "champ_size_2x", "champ_size_4x", "champ_size_8x",
              # cap2k_* deliberately test the $2k live-sizing geometry
              # (size x concurrent x turnover) — see 2026-05-27 $500/day plan.
              "cap2k_scalp", "cap2k_turnover", "cap2k_runner",
              "cap2k_concentrated", "cap2k_spread",
              # $2k winner-entry replicas (each clones a winner's entry @ $650x3)
              "cap2k_whales", "cap2k_deepdip", "cap2k_no_topping",
              "cap2k_volmin5k", "cap2k_regime",
              # $2k defended-spine candidate (7h-watch rec #1)
              "champion_defender_2k"}
    for c in catalog.configs:
        if c.bot_id in EXEMPT:
            continue
        assert c.base_position_usd == 20.0, (
            f"{c.bot_id}: base={c.base_position_usd}"
        )


# ============================================================================
# SP3 Block 1 — group-level filter tests
# ============================================================================

def test_no_macro_filters_diff(catalog, baseline):
    bot = _by_id(catalog)["no_macro_filters"]
    assert len(bot.filters_disabled) > 0
    assert bot.filters_enforced is None
    assert "filter_sol_macro_down" in bot.filters_disabled


def test_no_chart_pattern_filters_diff(catalog, baseline):
    bot = _by_id(catalog)["no_chart_pattern_filters"]
    assert len(bot.filters_disabled) > 0
    assert "filter_fake_bounce" in bot.filters_disabled


def test_no_structural_filters_diff(catalog, baseline):
    bot = _by_id(catalog)["no_structural_filters"]
    assert len(bot.filters_disabled) > 0
    assert "filter_topping" in bot.filters_disabled or "filter_mtf_strong_downtrend" in bot.filters_disabled


def test_no_timing_filters_diff(catalog, baseline):
    bot = _by_id(catalog)["no_timing_filters"]
    assert len(bot.filters_disabled) > 0


def test_no_flow_filters_diff(catalog, baseline):
    bot = _by_id(catalog)["no_flow_filters"]
    assert len(bot.filters_disabled) > 0


def test_no_liquidity_filters_diff(catalog, baseline):
    bot = _by_id(catalog)["no_liquidity_filters"]
    assert len(bot.filters_disabled) > 0


def test_group_bots_have_disjoint_categories(catalog):
    """Each filter should appear in exactly ONE group bot's filters_disabled,
    confirming the categorization is partition-style (no overlap)."""
    group_ids = ["no_macro_filters", "no_chart_pattern_filters",
                 "no_structural_filters", "no_timing_filters",
                 "no_flow_filters", "no_liquidity_filters"]
    by_id = _by_id(catalog)
    seen: dict[str, str] = {}
    for gid in group_ids:
        bot = by_id[gid]
        for f in bot.filters_disabled:
            if f in seen:
                pytest.fail(
                    f"filter {f} appears in both {seen[f]} and {gid} - "
                    "groups must be disjoint"
                )
            seen[f] = gid


# ============================================================================
# SP3 Block 2 — individual filter ablations (top 10 by block rate)
# ============================================================================

# The 10 individual ablation bots use short IDs (no_<filter_name_without_prefix>)
SP3_ABLATION_MAP = {
    "no_turn": "filter_turn",
    "no_negative_net_flow_5m": "filter_negative_net_flow_5m",
    "no_seller_imbalance": "filter_seller_imbalance",
    "no_low_volatility": "filter_low_volatility",
    "no_vp_poc": "filter_vp_poc",
    "no_topping": "filter_topping",
    "no_above_vwap_chase": "filter_above_vwap_chase",
    "no_bs_m5_weak": "filter_bs_m5_weak",
    "no_blowoff_top": "filter_blowoff_top",
    "no_1m_steep_fall": "filter_1m_steep_fall",
}


def test_all_10_individual_ablations_present(catalog):
    by_id = _by_id(catalog)
    for bot_id in SP3_ABLATION_MAP:
        assert bot_id in by_id, f"Missing ablation bot: {bot_id}"


def test_individual_ablations_disable_exactly_one_filter(catalog):
    by_id = _by_id(catalog)
    for bot_id, expected_filter in SP3_ABLATION_MAP.items():
        cfg = by_id[bot_id]
        assert len(cfg.filters_disabled) == 1, (
            f"{bot_id} disables {len(cfg.filters_disabled)} filters, expected 1"
        )
        assert cfg.filters_disabled[0] == expected_filter, (
            f"{bot_id} disables {cfg.filters_disabled[0]}, expected {expected_filter}"
        )


# ============================================================================
# SP3 Block 3 — threshold sweeps
# ============================================================================

def test_sol_h6_loose_diff(catalog, baseline):
    bot = _by_id(catalog)["sol_h6_loose"]
    assert bot.sol_macro_h6_block_threshold == -0.1
    assert baseline.sol_macro_h6_block_threshold == -0.3


def test_sol_h6_tight_diff(catalog, baseline):
    bot = _by_id(catalog)["sol_h6_tight"]
    assert bot.sol_macro_h6_block_threshold == -0.5


def test_sol_h6_extreme_diff(catalog, baseline):
    bot = _by_id(catalog)["sol_h6_extreme"]
    assert bot.sol_macro_h6_block_threshold == -1.0


def test_psych_h24_50_diff(catalog, baseline):
    bot = _by_id(catalog)["psych_h24_50"]
    assert bot.mcap_psych_pc_h24_max == 50.0
    assert baseline.mcap_psych_pc_h24_max == 80.0


def test_psych_h24_100_diff(catalog, baseline):
    bot = _by_id(catalog)["psych_h24_100"]
    assert bot.mcap_psych_pc_h24_max == 100.0


def test_psych_h24_150_diff(catalog, baseline):
    bot = _by_id(catalog)["psych_h24_150"]
    assert bot.mcap_psych_pc_h24_max == 150.0


def test_vol_min_500_diff(catalog, baseline):
    bot = _by_id(catalog)["vol_min_500"]
    assert bot.vol_h1_min == 500.0
    assert baseline.vol_h1_min == 1000.0


def test_vol_min_5k_diff(catalog, baseline):
    bot = _by_id(catalog)["vol_min_5k"]
    assert bot.vol_h1_min == 5000.0


def test_vol_min_10k_diff(catalog, baseline):
    bot = _by_id(catalog)["vol_min_10k"]
    assert bot.vol_h1_min == 10000.0
