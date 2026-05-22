"""Parity test for Sub-project 2 filter restructure (commit c5c590b).

The refactor changed all 40 ENFORCED filters in dip_scanner.py from
early-continue to observational. This test guards against future
regressions by:

1. Verifying the parity fixture loads (50 production-buy candidates)
2. Confirming the fixture has meaningful filter verdict data
3. Asserting structural invariants about the FeatureBundle the bots
   will see (filters_block field exists and accepts tuples)

The REAL parity validation is forward-watch on production buy rate
after deploy — if buy rate drops materially, the refactor regressed.
"""
import json
from pathlib import Path

import pytest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "filter_parity_candidates.json"


def test_fixture_loads():
    assert FIXTURE_PATH.exists(), "Run scripts/capture_filter_parity_fixture.py first"
    data = json.loads(FIXTURE_PATH.read_text())
    assert len(data["candidates"]) > 0


def test_fixture_has_meaningful_entry_meta():
    """At least 90% of candidates should have non-empty entry_meta."""
    data = json.loads(FIXTURE_PATH.read_text())
    with_meta = sum(1 for c in data["candidates"] if c.get("entry_meta"))
    assert with_meta >= len(data["candidates"]) * 0.9


def test_fixture_has_filter_verdict_fields():
    """At least 10% of candidates should have at least one filter_X_verdict=BLOCK
    field in entry_meta — confirms the production scanner is recording shadow
    filter verdicts (which the multi-bot variants will use to differentiate)."""
    data = json.loads(FIXTURE_PATH.read_text())
    candidates_with_shadow_blocks = 0
    for cand in data["candidates"]:
        meta = cand.get("entry_meta") or {}
        for key, val in meta.items():
            if key.endswith("_verdict") and val == "BLOCK":
                candidates_with_shadow_blocks += 1
                break
    assert candidates_with_shadow_blocks >= len(data["candidates"]) * 0.1, (
        f"Only {candidates_with_shadow_blocks}/{len(data['candidates'])} candidates "
        "show filter_verdict=BLOCK fields in entry_meta — fixture may be stale"
    )


def test_feature_bundle_accepts_filters_block_tuple():
    """The FeatureBundle's filters_block field must accept a tuple of
    filter names (the type the refactored scanner now produces)."""
    from core.feature_bundle import FeatureBundle

    bundle = FeatureBundle(
        token="TEST", address="a", pair_address="p", chain="solana",
        snapshot_ts=1.0, price_usd=0.001, mcap_usd=1_000_000.0,
        age_hours=24.0,
        pc_h24=None, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=None, bs_h1=None,
        sol_pc_h1=None, sol_pc_h4=None, sol_pc_h6=None, sol_pc_h24=None,
        btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,
        net_flow_15s_usd=None, net_flow_60s_usd=None, net_flow_5m_usd=None,
        top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None,
        cnn_cluster_id=None, fusion_outcome_prob=None,
        triggers_fired=(),
        triggers_shadow=(),
        filters_block=("filter_corpse", "filter_fake_bounce"),
        filters_pass=(),
        filters_shadow=(),
        raw_meta={},
    )
    assert bundle.filters_block == ("filter_corpse", "filter_fake_bounce")


def test_bot_evaluator_blocks_when_baseline_sees_filters_block():
    """Spot check: an evaluator with baseline-filter config (filters_enforced=None,
    filters_disabled=()) should block when filters_block has any filter."""
    from core.bot_config import BotConfig
    from core.bot_evaluator import BotEvaluator
    from core.feature_bundle import FeatureBundle

    cfg = BotConfig(bot_id="baseline_test", display_name="Baseline test")
    ev = BotEvaluator(cfg)
    bundle = FeatureBundle(
        token="TEST", address="a", pair_address="p", chain="solana",
        snapshot_ts=1.0, price_usd=0.001, mcap_usd=1_000_000.0,
        age_hours=24.0,
        pc_h24=None, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=50_000.0, bs_h1=None,
        sol_pc_h1=None, sol_pc_h4=None, sol_pc_h6=None, sol_pc_h24=None,
        btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,
        net_flow_15s_usd=None, net_flow_60s_usd=None, net_flow_5m_usd=None,
        top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None,
        cnn_cluster_id=None, fusion_outcome_prob=None,
        triggers_fired=("deep_1h_dip",),
        triggers_shadow=(),
        filters_block=("filter_corpse",),
        filters_pass=(),
        filters_shadow=(),
        raw_meta={},
    )
    decision = ev.evaluate(bundle)
    assert decision is None, "Baseline bot should respect filters_block"


def test_no_filters_bot_ignores_filters_block():
    """The no_filters bot config (filters_enforced=()) should buy even when
    filters_block has filters."""
    from core.bot_config import BotConfig
    from core.bot_evaluator import BotEvaluator
    from core.feature_bundle import FeatureBundle

    cfg = BotConfig(
        bot_id="no_filters_test", display_name="No filters test",
        filters_enforced=(),
    )
    ev = BotEvaluator(cfg)
    bundle = FeatureBundle(
        token="TEST", address="a", pair_address="p", chain="solana",
        snapshot_ts=1.0, price_usd=0.001, mcap_usd=1_000_000.0,
        age_hours=24.0,
        pc_h24=None, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=50_000.0, bs_h1=None,
        sol_pc_h1=None, sol_pc_h4=None, sol_pc_h6=None, sol_pc_h24=None,
        btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,
        net_flow_15s_usd=None, net_flow_60s_usd=None, net_flow_5m_usd=None,
        top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None,
        cnn_cluster_id=None, fusion_outcome_prob=None,
        triggers_fired=("deep_1h_dip",),
        triggers_shadow=(),
        filters_block=("filter_corpse", "filter_fake_bounce"),
        filters_pass=(),
        filters_shadow=(),
        raw_meta={},
    )
    decision = ev.evaluate(bundle)
    assert decision is not None, "no_filters bot should ignore filters_block"
    assert decision.token == "TEST"
