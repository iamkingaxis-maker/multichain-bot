import os
import pytest
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle
from core.bot_evaluator import BotEvaluator, BuyDecision, _rug_structure_blocks


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


# Fleet-wide rug-structure gate (2026-06-08)
def test_rug_structure_blocks_single_sided_or_no_buyers():
    # one-sided LP -> block
    assert _rug_structure_blocks(_bundle(raw_meta={"lp_single_sided": True}))[0] is True
    # zero unique buyers -> block
    assert _rug_structure_blocks(_bundle(raw_meta={"unique_buyers_n": 0}))[0] is True
    # healthy: two-sided + real buyers -> no block
    assert _rug_structure_blocks(
        _bundle(raw_meta={"lp_single_sided": False, "unique_buyers_n": 13}))[0] is False
    # both missing -> fail-OPEN (no block)
    assert _rug_structure_blocks(_bundle(raw_meta={}))[0] is False


def test_rug_gate_enforce_blocks_and_shadow_off_do_not(monkeypatch):
    ev = BotEvaluator(_cfg())
    rug = _bundle(raw_meta={"lp_single_sided": True})
    monkeypatch.setenv("RUG_GATE_MODE", "enforce")
    assert ev._rug_gate_blocks(rug) is True
    monkeypatch.setenv("RUG_GATE_MODE", "shadow")
    assert ev._rug_gate_blocks(rug) is False   # shadow logs but never blocks
    monkeypatch.setenv("RUG_GATE_MODE", "off")
    assert ev._rug_gate_blocks(rug) is False
    # default (unset) = enforce
    monkeypatch.delenv("RUG_GATE_MODE", raising=False)
    assert ev._rug_gate_blocks(rug) is True


def test_rug_gate_young_probe_exempt_from_zero_buyers(monkeypatch):
    """young_token_probe bots buy fresh (<2h) tokens BEFORE buyers accumulate,
    so unique_buyers_n==0 is the expected entry state, not a no-demand rug.
    Exempt them from THAT branch only — lp_single_sided still applies."""
    monkeypatch.setenv("RUG_GATE_MODE", "enforce")
    young = BotEvaluator(_cfg(young_token_probe=True))
    prod = BotEvaluator(_cfg())  # young_token_probe defaults False
    no_buyers = _bundle(raw_meta={"unique_buyers_n": 0})
    one_sided = _bundle(raw_meta={"lp_single_sided": True, "unique_buyers_n": 0})
    # young probe: NOT blocked by zero buyers (the silence bug fix)...
    assert young._rug_gate_blocks(no_buyers) is False
    # ...but STILL blocked by a one-sided LP (real rug signature)
    assert young._rug_gate_blocks(one_sided) is True
    # production bot: zero buyers still blocks (no regression)
    assert prod._rug_gate_blocks(no_buyers) is True


def test_rug_gate_default_enforce_blocks_via_evaluate(monkeypatch):
    monkeypatch.delenv("RUG_GATE_MODE", raising=False)  # default enforce
    ev = BotEvaluator(_cfg(triggers_allowed=("vol_breakout",)))
    # an otherwise-buyable token that is single-sided -> blocked outright
    decision = ev.evaluate(_bundle(
        triggers_fired=("vol_breakout",),
        raw_meta={"lp_single_sided": True}))
    assert decision is None


# One-shot-sniped 'bundle' rug gate via _rug_gate_blocks (2026-06-14, #437/#434)
def test_rug_bundle_gate_blocks_sniped_no_recurring(monkeypatch):
    monkeypatch.setenv("RUG_GATE_MODE", "off")        # isolate the bundle gate
    monkeypatch.setenv("RUG_BUNDLE_MODE", "enforce")
    ev = BotEvaluator(_cfg())                          # young_token_probe defaults False
    sniped = _bundle(raw_meta={"n_recurring_buyers_3plus": 0, "top10_buyer_time_spread_sec": 12})
    assert ev._rug_gate_blocks(sniped) is True
    organic = _bundle(raw_meta={"n_recurring_buyers_3plus": 2, "top10_buyer_time_spread_sec": 12})
    assert ev._rug_gate_blocks(organic) is False       # recurring buyers = real demand
    monkeypatch.setenv("RUG_BUNDLE_MODE", "shadow")
    assert ev._rug_gate_blocks(sniped) is False         # shadow logs, never blocks


def test_rug_bundle_gate_young_probe_exempt_unless_forced(monkeypatch):
    """young_token_probe exempts a bot from the bundle gate (fresh-launch thesis),
    UNLESS rug_bundle_gate_force opts it back in (the chameleon: young-probe for the
    zero-buyers carve-out, but must still dodge sniped-no-recurring rugs)."""
    monkeypatch.setenv("RUG_GATE_MODE", "off")
    monkeypatch.setenv("RUG_BUNDLE_MODE", "enforce")
    sniped = _bundle(raw_meta={"n_recurring_buyers_3plus": 0, "top10_buyer_time_spread_sec": 12})
    young = BotEvaluator(_cfg(young_token_probe=True))
    forced = BotEvaluator(_cfg(young_token_probe=True, rug_bundle_gate_force=True))
    assert young._rug_gate_blocks(sniped) is False       # exempt by young_token_probe
    assert forced._rug_gate_blocks(sniped) is True        # chameleon's forced opt-in


# Per-trigger token-state gates (2026-06-08)
def test_trigger_state_gate_drops_trigger_when_state_fails():
    cfg = _cfg(trigger_state_gates=[
        ["power_dip_runner", [["pc_m5", "<=", -2.43]]],
    ])
    ev = BotEvaluator(cfg)
    # state passes -> trigger kept
    assert ev._effective_triggers(
        _bundle(triggers_fired=("power_dip_runner",),
                raw_meta={"pc_m5": -5.0})) == ("power_dip_runner",)
    # state fails -> trigger dropped (won't count toward min_triggers_to_fire)
    assert ev._effective_triggers(
        _bundle(triggers_fired=("power_dip_runner",),
                raw_meta={"pc_m5": -1.0})) == ()
    # feature missing -> fail-OPEN, trigger kept (coverage-safe, matches entry_gate)
    assert ev._effective_triggers(
        _bundle(triggers_fired=("power_dip_runner",),
                raw_meta={})) == ("power_dip_runner",)


def test_trigger_state_gate_only_affects_gated_triggers():
    cfg = _cfg(trigger_state_gates=[
        ["power_dip_runner", [["pc_m5", "<=", -2.43]]],
    ])
    ev = BotEvaluator(cfg)
    # ungated trigger (chart_quality_bottom) passes through even when gated one fails
    out = set(ev._effective_triggers(
        _bundle(triggers_fired=("power_dip_runner", "chart_quality_bottom"),
                raw_meta={"pc_m5": -1.0})))
    assert out == {"chart_quality_bottom"}


def test_trigger_state_gates_default_none_no_change():
    ev = BotEvaluator(_cfg())  # no gates
    assert ev._effective_triggers(
        _bundle(triggers_fired=("power_dip_runner",),
                raw_meta={"pc_m5": -1.0})) == ("power_dip_runner",)


# Dead-flatline volatility floor (2026-06-02)
def test_min_volatility_floor_blocks_flatline():
    ev = BotEvaluator(_cfg(min_token_volatility_h24_pct=5.0))
    # vRse-type flatline (0.48% 24h vol) -> blocked
    assert ev._token_regime_passes(_bundle(raw_meta={"token_volatility_h24_pct": 0.48})) is False
    # normal token (30% vol) -> passes the floor
    assert ev._token_regime_passes(_bundle(raw_meta={"token_volatility_h24_pct": 30.0})) is True
    # missing feature -> fail-OPEN (protects young tokens lacking a 24h window)
    assert ev._token_regime_passes(_bundle(raw_meta={})) is True


def test_min_volatility_floor_off_by_default():
    ev = BotEvaluator(_cfg())  # min_token_volatility_h24_pct=None
    assert ev._token_regime_passes(_bundle(raw_meta={"token_volatility_h24_pct": 0.48})) is True


# Range-floor reject (2026-06-03) — replaces the 5% vol floor (strict superset)
def test_min_shape_90m_range_floor_blocks_flatline():
    ev = BotEvaluator(_cfg(min_shape_90m_range_pct=10.0))
    # dead-flatline (4% trailing-90m range) -> blocked
    assert ev._token_regime_passes(_bundle(raw_meta={"shape_90m_range_pct": 4.0})) is False
    # TREND-type runner (90m range 35%+) -> passes
    assert ev._token_regime_passes(_bundle(raw_meta={"shape_90m_range_pct": 35.1})) is True
    # missing feature -> fail-OPEN (token <90m old)
    assert ev._token_regime_passes(_bundle(raw_meta={})) is True


def test_min_shape_90m_range_floor_off_by_default():
    ev = BotEvaluator(_cfg())  # min_shape_90m_range_pct=None
    assert ev._token_regime_passes(_bundle(raw_meta={"shape_90m_range_pct": 4.0})) is True


# Momentum-continuation mode (#4.3)
def test_momentum_mode_enters_on_gate_bypassing_dip_triggers():
    # No dip trigger is allowed (normal path would return None for <min_triggers), and a
    # filter is enforced — momentum mode bypasses both and enters on the entry_gate.
    cfg = _cfg(momentum_mode=True,
               triggers_allowed=("nonexistent_dip_trigger",),
               filters_enforced=("filter_falling_pump",),
               entry_gate=[["pc_h1", ">=", 20], ["pct_above_vwap_h24", "<=", 20],
                           ["1m_volume_spike", ">=", 0.4]])
    d = BotEvaluator(cfg).evaluate(_bundle(
        pc_h1=30.0,
        raw_meta={"pc_h1": 30.0, "pct_above_vwap_h24": 10.0, "1m_volume_spike": 0.5}))
    assert d is not None
    assert d.triggers_fired == ("momentum_continuation",)
    assert d.size_tier == "momentum"


def test_momentum_mode_blocks_when_gate_fails():
    cfg = _cfg(momentum_mode=True, entry_gate=[["pc_h1", ">=", 20]])
    # pc_h1 below the momentum threshold -> no entry
    assert BotEvaluator(cfg).evaluate(_bundle(raw_meta={"pc_h1": 5.0})) is None


def test_momentum_mode_blocks_on_overextension_above_vwap():
    cfg = _cfg(momentum_mode=True,
               entry_gate=[["pc_h1", ">=", 20], ["pct_above_vwap_h24", "<=", 20]])
    # runner but chasing far above vwap (40% > 20 cap) -> blocked (no blow-off chase)
    assert BotEvaluator(cfg).evaluate(_bundle(
        raw_meta={"pc_h1": 30.0, "pct_above_vwap_h24": 40.0})) is None


# Fresh-graduation momentum probe (2026-06-03) — DATA-CALIBRATED order-flow gate
# (deep dive: net_flow_60s_imbalance is the dominant pumper-vs-dumper separator d=+0.98;
#  the originally-guessed 1m_cum_3min gate was BACKWARDS and was retired)
def _grad_gate():
    # 2026-06-04: added buyer-breadth condition (C) — reject whale-dominated buying
    # (large_buyer_volume_pct>0.5), the fresh-token bleed signature (fleet d=-0.80).
    return [["net_flow_60s_imbalance", ">=", 0.3], ["1m_volume_spike", ">=", 0.4],
            ["large_buyer_volume_pct", "<=", 0.5]]


def test_grad_momentum_enters_on_buy_flow():
    cfg = _cfg(momentum_mode=True, young_token_probe=True, entry_gate=_grad_gate())
    # fresh token with strong 60s buy-flow imbalance -> ENTER the rising leg
    d = BotEvaluator(cfg).evaluate(_bundle(age_hours=2.0, raw_meta={
        "net_flow_60s_imbalance": 0.55, "1m_volume_spike": 0.9}))
    assert d is not None and d.triggers_fired == ("momentum_continuation",)


def test_grad_momentum_blocks_on_weak_flow():
    cfg = _cfg(momentum_mode=True, young_token_probe=True, entry_gate=_grad_gate())
    # net flow flat/negative (the dumper signature) -> gate blocks
    assert BotEvaluator(cfg).evaluate(_bundle(age_hours=2.0, raw_meta={
        "net_flow_60s_imbalance": 0.02, "1m_volume_spike": 0.9})) is None


def test_grad_momentum_rejects_whale_dominated_buying():
    # C (2026-06-04): buyer-breadth 2nd gate condition large_buyer_volume_pct<=0.5
    # rejects whale-dominated buying (fresh-token bleed signature, fleet d=-0.80).
    cfg = _cfg(momentum_mode=True, young_token_probe=True, entry_gate=_grad_gate())
    base = {"net_flow_60s_imbalance": 0.55, "1m_volume_spike": 0.9}
    # whale-dominated buying -> gate rejects (one buyer owns >50% of buy volume)
    assert BotEvaluator(cfg).evaluate(_bundle(age_hours=2.0, raw_meta={
        **base, "large_buyer_volume_pct": 0.81})) is None
    # distributed buying -> enters
    assert BotEvaluator(cfg).evaluate(_bundle(age_hours=2.0, raw_meta={
        **base, "large_buyer_volume_pct": 0.10})) is not None
    # feature missing -> fail-open (enters, degrades to prior behavior)
    assert BotEvaluator(cfg).evaluate(_bundle(age_hours=2.0, raw_meta=base)) is not None


def test_grad_momentum_probe_config_loads():
    import json, pathlib
    p = pathlib.Path("config/bots/momentum_grad_probe.json")
    cfg = BotConfig(**json.loads(p.read_text()))
    assert cfg.momentum_mode is True and cfg.young_token_probe is True
    assert cfg.entry_gate  # data-calibrated order-flow gate
    # 10-slot no-same-token pool (pool_a style) for max fresh-graduation capture
    assert cfg.max_concurrent_positions == 10 and cfg.exclusion_pool == "momentum_grad"
    # data-informed exits for the ~70%-rug regime: TIME-based downside (stall/time-stop) + tail-ride ladder
    assert cfg.stall_exit_minutes == 40 and cfg.never_runner_minutes == 35 and cfg.tp2_pct == 60.0


# badday_flush_nf15_dense (2026-06-20 thin-tape-floor decode) — clone of
# badday_flush_nf15 with ONE added entry_gate clause net_flow_15s_n>=3. The
# net_flow_15s_n feature has IDENTICAL coverage to net_flow_15s_imbalance
# (both emitted together by tier3_features.compute_net_flow_windows when the
# 15s window has trades) and merges into raw_meta via _tier3_features, so the
# clause resolves in the same namespace the existing nf15 clause uses.
def test_badday_flush_nf15_dense_config_loads():
    import json, pathlib
    p = pathlib.Path("config/bots/badday_flush_nf15_dense.json")
    cfg = BotConfig(**json.loads(p.read_text()))
    assert cfg.bot_id == "badday_flush_nf15_dense"
    assert cfg.enabled is True
    assert cfg.exclusion_pool == "badday_flush_nf15_dense"
    # flat sizing, no conviction (identical to _nf15 control)
    assert cfg.base_position_usd == 100.0
    assert cfg.conviction_sizing_mode is None
    # paper-only — no live_probe field on the clone
    assert not getattr(cfg, "live_probe", None)
    # the new thin-tape-floor clause is the ONLY entry_gate difference vs _nf15
    nf15 = BotConfig(**json.loads(
        pathlib.Path("config/bots/badday_flush_nf15.json").read_text()))
    assert list(cfg.entry_gate) == list(nf15.entry_gate) + [("net_flow_15s_n", ">=", 3.0)]


def test_badday_flush_nf15_dense_gate_passes_on_dense_tape():
    # nf15>=0 AND net_flow_15s_n=5 (>=3) -> gate PASSES (dense demand window).
    cfg = _cfg(entry_gate=[["net_flow_15s_imbalance", ">=", 0],
                          ["net_flow_15s_n", ">=", 3]])
    assert BotEvaluator(cfg)._token_regime_passes(_bundle(raw_meta={
        "net_flow_15s_imbalance": 0.4, "net_flow_15s_n": 5})) is True


def test_badday_flush_nf15_dense_gate_blocks_thin_tape():
    # the saturated thin-tape trap: imbalance ok but only 1 trade in the
    # window (net_flow_15s_n=1 < 3) -> the new clause BLOCKS.
    cfg = _cfg(entry_gate=[["net_flow_15s_imbalance", ">=", 0],
                          ["net_flow_15s_n", ">=", 3]])
    assert BotEvaluator(cfg)._token_regime_passes(_bundle(raw_meta={
        "net_flow_15s_imbalance": 1.0, "net_flow_15s_n": 1})) is False


# Macro + regime gates (T9)
def test_evaluator_returns_buy_when_triggers_fire():
    ev = BotEvaluator(_cfg())
    d = ev.evaluate(_bundle())
    assert d is not None
    assert d.token == "TEST"
    assert d.size_usd == 20.0

def test_evaluator_skips_when_no_triggers_fire():
    d = BotEvaluator(_cfg()).evaluate(_bundle(triggers_fired=()))
    assert d is None

def test_evaluator_sol_macro_blocks_when_h6_below_threshold():
    d = BotEvaluator(_cfg(sol_macro_h6_block_threshold=-0.3)).evaluate(_bundle(sol_pc_h6=-0.5))
    assert d is None

def test_evaluator_sol_macro_allows_when_h6_above_threshold():
    d = BotEvaluator(_cfg(sol_macro_h6_block_threshold=-0.3)).evaluate(_bundle(sol_pc_h6=-0.1))
    assert d is not None

def test_evaluator_sol_macro_disabled_when_threshold_None():
    d = BotEvaluator(_cfg(
        sol_macro_h6_block_threshold=None,
        sol_macro_h1_block_threshold=None,
    )).evaluate(_bundle(sol_pc_h6=-5.0))
    assert d is not None

def test_evaluator_blocks_when_pc_h24_above_max():
    d = BotEvaluator(_cfg(pc_h24_max=80.0)).evaluate(_bundle(pc_h24=90.0))
    assert d is None

def test_evaluator_allows_when_pc_h24_under_max():
    d = BotEvaluator(_cfg(pc_h24_max=80.0)).evaluate(_bundle(pc_h24=50.0))
    assert d is not None

# Filter handling (T10)
def test_evaluator_blocks_when_baseline_filter_blocks():
    d = BotEvaluator(_cfg()).evaluate(_bundle(filters_block=("filter_corpse",)))
    assert d is None

def test_evaluator_allows_when_filter_disabled():
    d = BotEvaluator(_cfg(filters_disabled=("filter_corpse",))).evaluate(
        _bundle(filters_block=("filter_corpse",))
    )
    assert d is not None

def test_evaluator_allows_when_filter_not_in_enforced_list():
    d = BotEvaluator(_cfg(filters_enforced=("filter_fake_bounce",))).evaluate(
        _bundle(filters_block=("filter_corpse",))
    )
    assert d is not None

def test_evaluator_blocks_when_filter_in_enforced_list():
    d = BotEvaluator(_cfg(filters_enforced=("filter_corpse",))).evaluate(
        _bundle(filters_block=("filter_corpse",))
    )
    assert d is None

def test_evaluator_no_filters_config_ignores_all_filter_blocks():
    d = BotEvaluator(_cfg(filters_enforced=())).evaluate(
        _bundle(filters_block=("filter_corpse", "filter_fake_bounce"))
    )
    assert d is not None

# Sizing
def test_evaluator_alpha_trigger_gets_1_5x_size():
    d = BotEvaluator(_cfg()).evaluate(_bundle(triggers_fired=("1s_capit_reversal",)))
    assert d.size_usd == 30.0  # 20 * 1.5
    assert d.size_tier == "alpha_trigger"

def test_evaluator_demotes_1s_capit_reversal_alpha_at_pc_h24_80():
    # 1s_capit_reversal alone, pc_h24>=80 → demoted off alpha (9840ffe). Since it
    # is also in _MARGINAL_FOR_SIZE, the marginal multiplier (audit #6, 2026-05-27)
    # then applies once it's non-alpha → lands at marginal/10, MORE conservative
    # than the old standard/20. The WORLDCUP pc_h24>=80 protection is intact; the
    # load-bearing guarantee is simply "not alpha-sized". (Assertion updated for
    # the composed behavior — the test predated the 05-27 marginal wiring.)
    d = BotEvaluator(_cfg()).evaluate(_bundle(
        triggers_fired=("1s_capit_reversal",),
        pc_h24=85.0,
    ))
    assert d.size_tier != "alpha_trigger"   # load-bearing: NOT alpha-sized at pc_h24>=80
    assert d.size_usd == 10.0
    assert d.size_tier == "marginal"

def test_evaluator_mcap_psych_gated_by_pc_h24():
    # mcap_psych_level alone, pc_h24>=80 → no trigger → no buy
    d = BotEvaluator(_cfg()).evaluate(_bundle(
        triggers_fired=("mcap_psych_level",),
        pc_h24=85.0,
    ))
    assert d is None


# Compounding (2026-05-23)
def test_compound_linear_grows_with_realized_pnl():
    ev = BotEvaluator(_cfg(compound_mode="linear", paper_capital_usd=2000.0))
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",)), realized_pnl_usd=200.0)
    # +$200 realized on $2000 starting → 1.10 multiplier
    assert d.size_usd == pytest.approx(20.0 * 1.10)
    assert "compound_linear" in d.size_tier


def test_compound_linear_shrinks_on_loss():
    ev = BotEvaluator(_cfg(compound_mode="linear", paper_capital_usd=2000.0))
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",)), realized_pnl_usd=-100.0)
    # -$100 realized → 0.95 multiplier (above 0.25 floor)
    assert d.size_usd == pytest.approx(20.0 * 0.95)


def test_compound_linear_floored_at_25pct():
    ev = BotEvaluator(_cfg(compound_mode="linear", paper_capital_usd=2000.0))
    # -$1900 realized would imply 0.05x; floor at 0.25x → $5
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",)), realized_pnl_usd=-1900.0)
    assert d.size_usd == pytest.approx(20.0 * 0.25)


def test_compound_winners_only_does_not_shrink():
    ev = BotEvaluator(_cfg(compound_mode="winners_only", paper_capital_usd=2000.0))
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",)), realized_pnl_usd=-100.0)
    # Losses ignored → multiplier stays at 1.0
    assert d.size_usd == 20.0


def test_compound_winners_only_grows_on_wins():
    ev = BotEvaluator(_cfg(compound_mode="winners_only", paper_capital_usd=2000.0))
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",)), realized_pnl_usd=300.0)
    # +$300 / $2000 → 1.15 multiplier
    assert d.size_usd == pytest.approx(20.0 * 1.15)


def test_compound_threshold_steps_discrete():
    ev = BotEvaluator(_cfg(
        compound_mode="threshold",
        compound_threshold_step_usd=100.0,
        compound_step_amount_usd=5.0,
    ))
    # +$237 → 2 full steps → +$10 → $30
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",)), realized_pnl_usd=237.0)
    assert d.size_usd == 30.0


def test_compound_threshold_ignores_negative_realized():
    ev = BotEvaluator(_cfg(
        compound_mode="threshold",
        compound_threshold_step_usd=100.0,
        compound_step_amount_usd=5.0,
    ))
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",)), realized_pnl_usd=-500.0)
    # Negative realized → 0 steps → unchanged base
    assert d.size_usd == 20.0


def test_compound_capped_at_max_multiplier():
    ev = BotEvaluator(_cfg(
        compound_mode="linear",
        paper_capital_usd=2000.0,
        compound_max_multiplier=2.0,
    ))
    # +$10000 realized would imply 6x; cap to 2x
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",)), realized_pnl_usd=10_000.0)
    assert d.size_usd == 40.0


def test_compound_disabled_by_default():
    """Bots without compound_mode set ignore realized_pnl entirely."""
    ev = BotEvaluator(_cfg())  # no compound_mode
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",)), realized_pnl_usd=500.0)
    assert d.size_usd == 20.0
    assert "compound" not in d.size_tier


def test_compound_stacks_with_alpha_multiplier():
    """Compound multiplier is applied AFTER alpha tier, so an alpha trigger
    with +$200 realized at compound_linear gets 1.5x * 1.10x = 1.65x."""
    ev = BotEvaluator(_cfg(compound_mode="linear", paper_capital_usd=2000.0))
    d = ev.evaluate(
        _bundle(triggers_fired=("deep_1h_dip",)),  # alpha trigger
        realized_pnl_usd=200.0,
    )
    assert d.size_usd == pytest.approx(20.0 * 1.5 * 1.10)
    assert "alpha_trigger+compound_linear" in d.size_tier


# Trading-window gate (2026-05-23 — fixes TOD bots that had no enforcement)
from datetime import datetime, timezone


def _ts_at_hour_utc(hour: int) -> float:
    return datetime(2026, 5, 23, hour, 0, 0, tzinfo=timezone.utc).timestamp()


def test_default_window_always_fires():
    """Window 0..24 (default) never blocks — back-compat for non-TOD bots."""
    ev = BotEvaluator(_cfg())
    d = ev.evaluate(_bundle(snapshot_ts=_ts_at_hour_utc(15)))
    assert d is not None


def test_window_fires_inside_simple_range():
    """Window 6..12 + snapshot at 10 UTC → in-window → fires."""
    ev = BotEvaluator(_cfg(trading_hour_utc_start=6, trading_hour_utc_end=12))
    d = ev.evaluate(_bundle(snapshot_ts=_ts_at_hour_utc(10)))
    assert d is not None


def test_window_blocks_outside_simple_range():
    """Window 6..12 + snapshot at 15 UTC → out-of-window → blocks."""
    ev = BotEvaluator(_cfg(trading_hour_utc_start=6, trading_hour_utc_end=12))
    d = ev.evaluate(_bundle(snapshot_ts=_ts_at_hour_utc(15)))
    assert d is None


def test_window_blocks_at_end_boundary_simple():
    """Half-open: hour 12 is NOT in [6, 12). Should block."""
    ev = BotEvaluator(_cfg(trading_hour_utc_start=6, trading_hour_utc_end=12))
    d = ev.evaluate(_bundle(snapshot_ts=_ts_at_hour_utc(12)))
    assert d is None


def test_window_fires_inside_wrap_around():
    """Wrap window 22..2 (start > end) + snapshot at 1 or 23 → in-window."""
    ev = BotEvaluator(_cfg(trading_hour_utc_start=22, trading_hour_utc_end=2))
    assert ev.evaluate(_bundle(snapshot_ts=_ts_at_hour_utc(1))) is not None
    assert ev.evaluate(_bundle(snapshot_ts=_ts_at_hour_utc(23))) is not None


def test_window_blocks_outside_wrap_around():
    """Wrap window 22..2 + snapshot at 12 UTC → out-of-window → blocks."""
    ev = BotEvaluator(_cfg(trading_hour_utc_start=22, trading_hour_utc_end=2))
    d = ev.evaluate(_bundle(snapshot_ts=_ts_at_hour_utc(12)))
    assert d is None


# Drawdown freeze (Deploy C 2026-05-23)
def test_drawdown_freeze_blocks_when_realized_at_or_below_threshold():
    ev = BotEvaluator(_cfg(drawdown_freeze_threshold_usd=-100.0))
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",)), realized_pnl_usd=-100.0)
    assert d is None
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",)), realized_pnl_usd=-200.0)
    assert d is None


def test_drawdown_freeze_allows_when_realized_above_threshold():
    ev = BotEvaluator(_cfg(drawdown_freeze_threshold_usd=-100.0))
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",)), realized_pnl_usd=-99.0)
    assert d is not None


def test_drawdown_freeze_disabled_by_default():
    ev = BotEvaluator(_cfg())  # threshold None
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",)), realized_pnl_usd=-10000.0)
    assert d is not None  # never blocks when threshold None


# Macro-conditional sizing (Deploy C 2026-05-23)
def test_macro_conditional_bull_sizes_up():
    ev = BotEvaluator(_cfg(macro_conditional_mode="sol_h6",
                            sol_macro_h6_block_threshold=None,
                            sol_macro_h1_block_threshold=None))
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",), sol_pc_h6=0.5))
    assert d.size_usd == 30.0  # 20 * 1.5
    assert "macro_bull" in d.size_tier


def test_macro_conditional_bear_sizes_down():
    ev = BotEvaluator(_cfg(macro_conditional_mode="sol_h6",
                            sol_macro_h6_block_threshold=None,
                            sol_macro_h1_block_threshold=None))
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",), sol_pc_h6=-0.2))
    assert d.size_usd == 10.0  # 20 * 0.5
    assert "macro_bear" in d.size_tier


def test_macro_conditional_neutral_unchanged():
    ev = BotEvaluator(_cfg(macro_conditional_mode="sol_h6",
                            sol_macro_h6_block_threshold=None,
                            sol_macro_h1_block_threshold=None))
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",), sol_pc_h6=0.1))
    assert d.size_usd == 20.0
    assert "macro_neutral" in d.size_tier


def test_macro_conditional_disabled_by_default():
    ev = BotEvaluator(_cfg())  # mode None
    d = ev.evaluate(_bundle(triggers_fired=("vol_breakout",), sol_pc_h6=0.5))
    assert d.size_usd == 20.0  # no scaling
    assert "macro" not in d.size_tier


# Fleet-wide validated entry-stack gate (2026-06-09)
def test_entry_stack_violations_each_gate():
    from core.bot_evaluator import _entry_stack_violations
    # shallow dip -> violation
    assert any("dip_shallow" in f for f in _entry_stack_violations(
        _bundle(raw_meta={"shape_90m_drawdown_from_max_pct": -10.0})))
    # deep dip -> clean
    assert not _entry_stack_violations(
        _bundle(raw_meta={"shape_90m_drawdown_from_max_pct": -22.0}))
    # weak flow -> violation
    assert any("flow_weak" in f for f in _entry_stack_violations(
        _bundle(raw_meta={"net_flow_60s_usd": 12.0})))
    # strong flow -> clean
    assert not _entry_stack_violations(
        _bundle(raw_meta={"net_flow_60s_usd": 350.0}))
    # young token -> violation; age 0 (unknown) fails OPEN
    assert any("age_young" in f for f in _entry_stack_violations(_bundle(age_hours=3.0)))
    assert not _entry_stack_violations(_bundle(age_hours=0.0))
    # mcap out of band -> violation; mcap 0 (unknown) fails OPEN
    assert any("mcap_out" in f for f in _entry_stack_violations(_bundle(mcap_usd=120_000.0)))
    assert not _entry_stack_violations(_bundle(mcap_usd=0.0))
    # all features missing -> fail-OPEN (no violations)
    assert _entry_stack_violations(_bundle(raw_meta={})) == []


def test_entry_stack_modes_and_default_enforce(monkeypatch):
    ev = BotEvaluator(_cfg())
    bad = _bundle(raw_meta={"shape_90m_drawdown_from_max_pct": -5.0})
    monkeypatch.setenv("ENTRY_STACK_MODE", "enforce")
    assert ev._entry_stack_blocks(bad) is True
    monkeypatch.setenv("ENTRY_STACK_MODE", "shadow")
    assert ev._entry_stack_blocks(bad) is False   # shadow never blocks
    monkeypatch.setenv("ENTRY_STACK_MODE", "off")
    assert ev._entry_stack_blocks(bad) is False
    monkeypatch.delenv("ENTRY_STACK_MODE", raising=False)
    assert ev._entry_stack_blocks(bad) is True    # default = enforce


def test_entry_stack_control_cohort_exempt(monkeypatch):
    monkeypatch.delenv("ENTRY_STACK_MODE", raising=False)   # enforce
    monkeypatch.delenv("ENTRY_STACK_CONTROL_BOTS", raising=False)
    bad = _bundle(raw_meta={"shape_90m_drawdown_from_max_pct": -5.0,
                            "net_flow_60s_usd": 0.0})
    # default control bots stay ungated
    for ctl in ("baseline_v1", "no_filters", "pool_a_broad_control"):
        assert BotEvaluator(_cfg(bot_id=ctl))._entry_stack_blocks(bad) is False
    # non-control bot is gated
    assert BotEvaluator(_cfg(bot_id="champ_runner"))._entry_stack_blocks(bad) is True
    # env override replaces the cohort
    monkeypatch.setenv("ENTRY_STACK_CONTROL_BOTS", "champ_runner")
    assert BotEvaluator(_cfg(bot_id="champ_runner"))._entry_stack_blocks(bad) is False
    assert BotEvaluator(_cfg(bot_id="baseline_v1"))._entry_stack_blocks(bad) is True


def test_entry_stack_blocks_via_evaluate(monkeypatch):
    monkeypatch.delenv("ENTRY_STACK_MODE", raising=False)   # default enforce
    ev = BotEvaluator(_cfg(triggers_allowed=("vol_breakout",)))
    # otherwise-buyable token with a shallow dip -> blocked outright
    assert ev.evaluate(_bundle(
        triggers_fired=("vol_breakout",),
        raw_meta={"shape_90m_drawdown_from_max_pct": -9.0})) is None
    # same token passing the full stack -> buys
    d = ev.evaluate(_bundle(
        triggers_fired=("vol_breakout",),
        raw_meta={"shape_90m_drawdown_from_max_pct": -22.0,
                  "net_flow_60s_usd": 400.0}))
    assert isinstance(d, BuyDecision)


# Post-stack filter prune (2026-06-09)
def test_post_stack_prune_gated_bot_ignores_pruned_filter(monkeypatch):
    monkeypatch.delenv("ENTRY_STACK_MODE", raising=False)          # enforce
    monkeypatch.delenv("ENTRY_STACK_FILTER_PRUNE", raising=False)  # on
    monkeypatch.delenv("ENTRY_STACK_CONTROL_BOTS", raising=False)
    ev = BotEvaluator(_cfg(bot_id="champ_runner"))
    # pruned filter blocking -> ignored for a gated bot
    assert ev._effective_filter_blocks(
        _bundle(filters_block=("filter_turn",))) is False
    # non-pruned filter still blocks
    assert ev._effective_filter_blocks(
        _bundle(filters_block=("filter_topping",))) is True


def test_post_stack_prune_control_bot_keeps_full_filters(monkeypatch):
    monkeypatch.delenv("ENTRY_STACK_MODE", raising=False)
    monkeypatch.delenv("ENTRY_STACK_FILTER_PRUNE", raising=False)
    monkeypatch.delenv("ENTRY_STACK_CONTROL_BOTS", raising=False)
    # control cohort is ungated -> pruned filters STILL block (old behavior)
    ev = BotEvaluator(_cfg(bot_id="baseline_v1"))
    assert ev._effective_filter_blocks(
        _bundle(filters_block=("filter_turn",))) is True


def test_post_stack_prune_kill_switch_and_explicit_enforced(monkeypatch):
    monkeypatch.delenv("ENTRY_STACK_MODE", raising=False)
    monkeypatch.delenv("ENTRY_STACK_CONTROL_BOTS", raising=False)
    ev = BotEvaluator(_cfg(bot_id="champ_runner"))
    # kill-switch off -> pruned filter blocks again (no deploy needed)
    monkeypatch.setenv("ENTRY_STACK_FILTER_PRUNE", "off")
    assert ev._effective_filter_blocks(
        _bundle(filters_block=("filter_turn",))) is True
    monkeypatch.delenv("ENTRY_STACK_FILTER_PRUNE", raising=False)
    # entry stack off -> prune also off (prune only makes sense post-stack)
    monkeypatch.setenv("ENTRY_STACK_MODE", "off")
    assert ev._effective_filter_blocks(
        _bundle(filters_block=("filter_turn",))) is True
    monkeypatch.delenv("ENTRY_STACK_MODE", raising=False)
    # explicit filters_enforced list is ALWAYS respected verbatim (no prune)
    ev2 = BotEvaluator(_cfg(bot_id="champ_runner",
                            filters_enforced=("filter_turn",)))
    assert ev2._effective_filter_blocks(
        _bundle(filters_block=("filter_turn",))) is True
