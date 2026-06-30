"""FILTERS_RELAX_LIST — fleet-wide env removal of audit-flagged winner-killer
filters from enforcement (2026-06-30 realized stack audit). Both the explicit
filters_enforced path and the default path subtract the relax set."""
from core.bot_config import BotConfig
from core.bot_evaluator import BotEvaluator, _globally_relaxed_filters
from core.feature_bundle import FeatureBundle


def _bundle(**ov):
    d = dict(
        token="TEST", address="a", pair_address="p", chain="solana",
        snapshot_ts=1716480000.0, price_usd=0.001, mcap_usd=4_000_000.0,
        age_hours=240.0, pc_h24=None, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=50_000.0, bs_h1=None, sol_pc_h1=None, sol_pc_h4=None,
        sol_pc_h6=None, sol_pc_h24=None, btc_pc_h1=None, btc_pc_h6=None,
        btc_bs_h1=None, net_flow_15s_usd=None, net_flow_60s_usd=None,
        net_flow_5m_usd=None, top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None, cnn_cluster_id=None,
        fusion_outcome_prob=None, triggers_fired=("vol_breakout",),
        triggers_shadow=(), filters_block=(), filters_pass=(), filters_shadow=(),
        raw_meta={},
    )
    d.update(ov)
    return FeatureBundle(**d)


def test_relax_list_parsing(monkeypatch):
    monkeypatch.delenv("FILTERS_RELAX_LIST", raising=False)
    assert _globally_relaxed_filters() == frozenset()
    monkeypatch.setenv("FILTERS_RELAX_LIST", "filter_blowoff_top, filter_chasing_bounce ,")
    assert _globally_relaxed_filters() == frozenset(
        {"filter_blowoff_top", "filter_chasing_bounce"})  # trims + drops empties


def test_enforced_path_relax(monkeypatch):
    cfg = BotConfig(bot_id="b1", display_name="B1",
                    filters_enforced=("filter_blowoff_top",))
    ev = BotEvaluator(cfg)
    b = _bundle(filters_block=("filter_blowoff_top",))
    monkeypatch.delenv("FILTERS_RELAX_LIST", raising=False)
    assert ev._effective_filter_blocks(b) is True            # enforced -> blocks
    monkeypatch.setenv("FILTERS_RELAX_LIST", "filter_blowoff_top")
    assert ev._effective_filter_blocks(b) is False           # relaxed -> no block


def test_relax_does_not_leak_other_filters(monkeypatch):
    cfg = BotConfig(bot_id="b1", display_name="B1",
                    filters_enforced=("filter_blowoff_top", "filter_vp_poc"))
    ev = BotEvaluator(cfg)
    b = _bundle(filters_block=("filter_vp_poc",))             # a KEEPER still fires
    monkeypatch.setenv("FILTERS_RELAX_LIST", "filter_blowoff_top")
    assert ev._effective_filter_blocks(b) is True            # vp_poc still blocks
