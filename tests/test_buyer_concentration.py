"""Buyer-concentration verdict (core/buyer_concentration). Whale-dominated BUYING
(large_buyer_volume_pct >= thr) is the fresh-token bleed signature (fleet d=-0.80)."""
from core.buyer_concentration import buyer_concentration_verdict


def test_whale_dominated_blocks():
    v, reasons = buyer_concentration_verdict({"large_buyer_volume_pct": 0.81})
    assert v == "BLOCK" and reasons


def test_distributed_passes():
    v, reasons = buyer_concentration_verdict({"large_buyer_volume_pct": 0.10})
    assert v == "PASS" and reasons == []


def test_zero_passes():
    assert buyer_concentration_verdict({"large_buyer_volume_pct": 0.0})[0] == "PASS"


def test_missing_is_neutral_fail_open():
    assert buyer_concentration_verdict({})[0] == "NEUTRAL"
    assert buyer_concentration_verdict({"large_buyer_volume_pct": None})[0] == "NEUTRAL"
    assert buyer_concentration_verdict({"large_buyer_volume_pct": True})[0] == "NEUTRAL"


def test_threshold_boundary():
    # exactly at threshold = BLOCK (>=); just below = PASS
    assert buyer_concentration_verdict({"large_buyer_volume_pct": 0.5})[0] == "BLOCK"
    assert buyer_concentration_verdict({"large_buyer_volume_pct": 0.49})[0] == "PASS"


def test_threshold_env_override(monkeypatch):
    monkeypatch.setenv("BUYER_CONC_BLOCK_THR", "0.7")
    assert buyer_concentration_verdict({"large_buyer_volume_pct": 0.6})[0] == "PASS"
    assert buyer_concentration_verdict({"large_buyer_volume_pct": 0.75})[0] == "BLOCK"
