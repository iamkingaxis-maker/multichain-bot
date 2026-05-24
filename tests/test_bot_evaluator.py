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
    # 1s_capit_reversal alone, pc_h24=80 → no alpha
    d = BotEvaluator(_cfg()).evaluate(_bundle(
        triggers_fired=("1s_capit_reversal",),
        pc_h24=85.0,
    ))
    assert d.size_usd == 20.0
    assert d.size_tier == "standard"

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
