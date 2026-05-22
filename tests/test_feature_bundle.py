import pytest
from dataclasses import FrozenInstanceError
from core.feature_bundle import FeatureBundle


def _make_bundle(**overrides):
    defaults = dict(
        token="TEST",
        address="addr1",
        pair_address="pair1",
        chain="solana",
        snapshot_ts=1716480000.0,
        price_usd=0.001,
        mcap_usd=4_000_000.0,
        age_hours=240.0,
        pc_h24=None, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=None, bs_h1=None,
        sol_pc_h1=None, sol_pc_h4=None, sol_pc_h6=None,
        sol_pc_h24=None, btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,
        net_flow_15s_usd=None, net_flow_60s_usd=None, net_flow_5m_usd=None,
        top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None,
        cnn_cluster_id=None, fusion_outcome_prob=None,
        triggers_fired=(),
        triggers_shadow=(),
        filters_block=(),
        filters_pass=(),
        filters_shadow=(),
        raw_meta={},
    )
    defaults.update(overrides)
    return FeatureBundle(**defaults)


def test_feature_bundle_immutable():
    b = _make_bundle()
    with pytest.raises(FrozenInstanceError):
        b.price_usd = 0.002


def test_feature_bundle_fields_accessible():
    b = _make_bundle(
        pc_h24=70.5,
        triggers_fired=("mcap_psych_level", "deep_1h_dip"),
    )
    assert b.token == "TEST"
    assert b.pc_h24 == 70.5
    assert "mcap_psych_level" in b.triggers_fired


def test_feature_bundle_optional_fields_default_none():
    b = _make_bundle()
    assert b.pc_h24 is None
    assert b.sol_pc_h1 is None
    assert b.cnn_cluster_id is None


def test_feature_bundle_tuples_are_immutable():
    b = _make_bundle(triggers_fired=("a", "b"))
    # tuples are immutable by Python semantics, but ensure we can't mutate via tuple+=
    with pytest.raises(FrozenInstanceError):
        b.triggers_fired = ("c",)
