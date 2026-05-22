import pytest
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle
from core.bot_evaluator import BotEvaluator, BuyDecision
from core.bot_manager import BotManager


def _bundle():
    return FeatureBundle(
        token="TEST", address="a", pair_address="p", chain="solana",
        snapshot_ts=1.0, price_usd=0.001, mcap_usd=4_000_000.0, age_hours=240.0,
        pc_h24=None, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=50_000.0, bs_h1=None,
        sol_pc_h1=None, sol_pc_h4=None, sol_pc_h6=None, sol_pc_h24=None,
        btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,
        net_flow_15s_usd=None, net_flow_60s_usd=None, net_flow_5m_usd=None,
        top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None,
        cnn_cluster_id=None, fusion_outcome_prob=None,
        triggers_fired=("vol_breakout",),  # non-alpha so std size
        triggers_shadow=(), filters_block=(), filters_pass=(), filters_shadow=(),
        raw_meta={},
    )


def test_bot_manager_fans_out_to_all_bots():
    cfgs = [
        BotConfig(bot_id="b1", display_name="B1"),
        BotConfig(bot_id="b2", display_name="B2"),
        BotConfig(bot_id="b3", display_name="B3"),
    ]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    decisions = mgr.evaluate_all(_bundle())
    assert len(decisions) == 3
    assert {d.bot_id for d in decisions} == {"b1", "b2", "b3"}


def test_bot_manager_isolates_exceptions_in_one_bot():
    class _BoomEvaluator(BotEvaluator):
        def evaluate(self, b):
            raise RuntimeError("boom")
    cfgs = [
        BotConfig(bot_id="ok", display_name="OK"),
        BotConfig(bot_id="boom", display_name="Boom"),
        BotConfig(bot_id="also_ok", display_name="Also OK"),
    ]
    evaluators = [
        BotEvaluator(cfgs[0]),
        _BoomEvaluator(cfgs[1]),
        BotEvaluator(cfgs[2]),
    ]
    mgr = BotManager(evaluators=evaluators)
    decisions = mgr.evaluate_all(_bundle())
    assert len(decisions) == 2
    assert {d.bot_id for d in decisions} == {"ok", "also_ok"}


def test_bot_manager_skips_disabled_bots():
    cfgs = [
        BotConfig(bot_id="b1", display_name="B1", enabled=True),
        BotConfig(bot_id="b2", display_name="B2", enabled=False),
    ]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    decisions = mgr.evaluate_all(_bundle())
    assert len(decisions) == 1
    assert decisions[0].bot_id == "b1"


def test_bot_manager_enabled_bot_ids():
    cfgs = [
        BotConfig(bot_id="b1", display_name="B1", enabled=True),
        BotConfig(bot_id="b2", display_name="B2", enabled=False),
        BotConfig(bot_id="b3", display_name="B3", enabled=True),
    ]
    mgr = BotManager(evaluators=[BotEvaluator(c) for c in cfgs])
    assert mgr.enabled_bot_ids() == ["b1", "b3"]
