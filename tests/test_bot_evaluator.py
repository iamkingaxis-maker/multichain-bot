import pytest
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle
from core.bot_evaluator import BotEvaluator, BuyDecision


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


# Fresh-graduation momentum probe (2026-06-03) — early-strength entry on the rising leg
def _grad_gate():
    return [["1m_cum_3min_pct", ">=", 2.0], ["1m_volume_spike", ">=", 0.5],
            ["vol_5m_burst_vs_h1", ">=", 1.3]]


def test_grad_momentum_enters_on_early_strength():
    cfg = _cfg(momentum_mode=True, young_token_probe=True, entry_gate=_grad_gate())
    # fresh token making early strength: rising 1m + volume accelerating -> ENTER the rising leg
    d = BotEvaluator(cfg).evaluate(_bundle(age_hours=2.0, raw_meta={
        "1m_cum_3min_pct": 4.0, "1m_volume_spike": 0.9, "vol_5m_burst_vs_h1": 1.8}))
    assert d is not None and d.triggers_fired == ("momentum_continuation",)


def test_grad_momentum_blocks_when_not_rising():
    cfg = _cfg(momentum_mode=True, young_token_probe=True, entry_gate=_grad_gate())
    # flat/falling 1m (the post-peak dip the dip-stack would buy) -> momentum gate blocks
    assert BotEvaluator(cfg).evaluate(_bundle(age_hours=2.0, raw_meta={
        "1m_cum_3min_pct": -1.0, "1m_volume_spike": 0.9, "vol_5m_burst_vs_h1": 1.8})) is None


def test_grad_momentum_probe_config_loads():
    import json, pathlib
    p = pathlib.Path("config/bots/momentum_grad_probe.json")
    cfg = BotConfig(**json.loads(p.read_text()))
    assert cfg.momentum_mode is True and cfg.young_token_probe is True
    assert cfg.entry_gate and cfg.tp2_pct == 30.0  # wide exit to ride the run


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
