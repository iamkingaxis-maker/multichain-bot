"""Verify the active bot catalog: each loads, each differs from baseline by exactly
the expected fields, and there are no duplicate bot_ids. 2026-06-02: removed 41 obsolete
tests for bots retired to config/bots/*.json.off (fleet slimmed to 55 active); the
probe_* live-measurement scaffold is excluded from these strategy-catalog assertions."""
import pytest
from pathlib import Path
from core.bot_registry import BotRegistry
from core.bot_config import BotConfig


@pytest.fixture(scope="module")
def catalog():
    reg = BotRegistry.from_directory(Path(__file__).parent.parent / "config" / "bots")
    # Exclude the live-measurement-probe scaffold (probe_*, 2026-06-02) — it is not a
    # strategy-ablation catalog member (it's a dormant live-execution clone), so it must
    # not perturb the catalog count / baseline-diff assertions.
    reg.configs = [c for c in reg.configs if not c.bot_id.startswith("probe_")]
    return reg


@pytest.fixture(scope="module")
def baseline(catalog):
    by_id = {c.bot_id: c for c in catalog.configs}
    return by_id["baseline_v1"]


def _by_id(catalog):
    return {c.bot_id: c for c in catalog.configs}


def test_catalog_active_bot_count(catalog):
    # 55 ACTIVE bots (enabled or not, loaded from config/bots/*.json). Retired bots live
    # on disk as *.json.off and are NOT loaded. The probe_* live scaffold is excluded by
    # the catalog fixture. Update this number deliberately when adding/retiring a bot.
    assert len(catalog.configs) == 55, (
        f"Expected 55 active bots, got {len(catalog.configs)}: "
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
        "filter_dead_volume", "filter_huge_wick",
    }

    # champion_defender_v4: v3's exact filter set, but NO stall_exit (WR-preserving A/B)
    v4 = by_id["champion_defender_v4"]
    assert set(v4.filters_enforced) == set(v3.filters_enforced)
    assert v4.stall_exit_minutes is None
    assert v4.base_position_usd == 20.0

    # champion_defender_2k (7h-watch rec #1): 8-filter defender on cap2k $2k spine
    d2k = by_id["champion_defender_2k"]
    assert d2k.filters_enforced is not None
    assert set(d2k.filters_enforced) == set(v3.filters_enforced)
    assert d2k.base_position_usd == 650.0
    assert d2k.stall_exit_minutes == 90

    # champion_premium (2026-05-29): v4's EXACT defender stack + exits, single
    # variable = triggers_allowed restricted to the two held-out-validated premium
    # triggers (deep_1h_dip + pullback_in_uptrend, +9pp WR edge over fleet in
    # train AND test). Clean A/B vs v4 (same stack, all triggers).
    prem = by_id["champion_premium"]
    assert set(prem.filters_enforced) == set(v4.filters_enforced)
    # 4 bad-day-robust triggers: beat fleet WR in train AND test AND on the
    # brutal 05-28 day (fleet 23% WR). Fragile good-day artifacts excluded.
    assert set(prem.triggers_allowed) == {
        "deep_1h_dip", "pullback_in_uptrend",
        "power_dip_runner", "chart_quality_bottom"}
    # 2026-05-31: drift-based stall_exit (90min/peak<=5%/drift>=2pp) added to the 4
    # gated champions — the exit-side dud RECYCLE complement to the never-green
    # scorer. Recycles deteriorating bought-duds the scorer didn't catch at entry,
    # without killing late bloomers (drift condition selects deteriorating, not flat).
    assert prem.stall_exit_minutes == 90
    assert prem.base_position_usd == 20.0

    # champion_whale_buyers (2026-05-29): v4's exact stack + a single entry_gate
    # on top_buy_makers_n<=8 (concentrated whales). Strongest feature-gate scan
    # survivor: beats fleet WR in both windows AND on the brutal 05-28 day, and
    # is +$/tr-positive in both regimes. triggers open (gate is the variable).
    whale = by_id["champion_whale_buyers"]
    assert set(whale.filters_enforced) == set(v4.filters_enforced)
    assert whale.triggers_allowed is None
    assert whale.entry_gate == (("top_buy_makers_n", "<=", 8.0),)
    assert whale.base_position_usd == 20.0

    # champion_post_peak (2026-05-29): v4's exact stack + single entry_gate on
    # time_since_h24_peak_secs>=14400 (>=4h past the 24h peak — "buy past the top,
    # not near it"). Broad feature-scan survivor; non-monotonic (mid-range is a
    # test disaster) so it must be a >= gate at the 4h boundary, not a tercile.
    pp = by_id["champion_post_peak"]
    assert set(pp.filters_enforced) == set(v4.filters_enforced)
    assert pp.triggers_allowed is None
    assert pp.entry_gate == (("time_since_h24_peak_secs", ">=", 14400.0),)
    assert pp.base_position_usd == 20.0

    # champion_premium_fresh (2026-05-30): champion_premium clone + the freshness
    # entry_gate (1m_volume_spike>=0.40 AND 1m_cum_3min_pct>=-3). Same triggers as
    # premium; premium stays the pure control for the freshness A/B.
    pf = by_id["champion_premium_fresh"]
    assert set(pf.triggers_allowed) == set(prem.triggers_allowed)
    assert set(pf.filters_enforced) == set(prem.filters_enforced)
    assert pf.entry_gate == (("1m_volume_spike", ">=", 0.40), ("1m_cum_3min_pct", ">=", -3.0))
    assert pf.base_position_usd == 20.0

    # champion_premium_tightexit (2026-05-31): IDENTICAL to champion_premium_fresh
    # except a tighter exit ladder (trail_pp 3.0->1.5, tp2 10->7) to plug the
    # trailing-stop leak (live trail exits gave back 5.3pp / captured only 11% of
    # peak). A/B vs fresh isolates exit aggressiveness; everything else (gate,
    # filters, triggers, scorer) matches. Forward-judge avg_win at n>=50.
    tx = by_id["champion_premium_tightexit"]
    assert tx.trail_pp == 1.5 and tx.tp2_pct == 7.0
    assert tx.entry_gate == pf.entry_gate
    assert set(tx.triggers_allowed) == set(pf.triggers_allowed)
    assert tx.ng_scorer_gate is True and tx.stall_exit_minutes == 90


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


def test_stop_8_config(catalog):
    assert _by_id(catalog)["stop_8"].hard_stop_pct == -8.0


def test_tp_runner_config(catalog):
    bot = _by_id(catalog)["tp_runner"]
    assert bot.tp1_pct == 8.0
    assert bot.tp2_pct == 20.0


def test_compound_winners_only_config(catalog):
    bot = _by_id(catalog)["compound_winners_only"]
    assert bot.compound_mode == "winners_only"


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


def test_wide_stop_diff(catalog, baseline):
    bot = _by_id(catalog)["wide_stop"]
    assert bot.hard_stop_pct == -20.0


# Thesis bots


def test_runner_tilt_aggressive_diff(catalog, baseline):
    bot = _by_id(catalog)["runner_tilt_aggressive"]
    assert bot.tp1_pct == 8.0
    assert bot.tp1_sell_fraction == 0.33
    assert bot.tp2_pct == 20.0
    assert bot.tp2_sell_fraction == 0.33
    assert bot.trail_pp == 4.0


def test_regime_aware_bullish_diff(catalog, baseline):
    bot = _by_id(catalog)["regime_aware_bullish"]
    assert bot.sol_macro_h1_block_threshold == 0.0
    assert bot.btc_macro_h1_block_threshold == 0.0


# Trigger-set isolation bots


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


# ============================================================================
# SP3 Block 3 — threshold sweeps
# ============================================================================

def test_sol_h6_loose_diff(catalog, baseline):
    bot = _by_id(catalog)["sol_h6_loose"]
    assert bot.sol_macro_h6_block_threshold == -0.1
    assert baseline.sol_macro_h6_block_threshold == -0.3


def test_psych_h24_100_diff(catalog, baseline):
    bot = _by_id(catalog)["psych_h24_100"]
    assert bot.mcap_psych_pc_h24_max == 100.0


def test_vol_min_500_diff(catalog, baseline):
    bot = _by_id(catalog)["vol_min_500"]
    assert bot.vol_h1_min == 500.0
    assert baseline.vol_h1_min == 1000.0


def test_vol_min_5k_diff(catalog, baseline):
    bot = _by_id(catalog)["vol_min_5k"]
    assert bot.vol_h1_min == 5000.0


