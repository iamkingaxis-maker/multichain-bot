"""Tests for core/holder_features.compute_holder_features."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.holder_features import compute_holder_features


def test_top_concentration_excludes_insiders_and_lp():
    rc = {
        "topHolders": [
            {"pct": 20.0, "tag": ""},                 # real, top1
            {"pct": 30.0, "tag": "Liquidity"},        # LP -> excluded
            {"pct": 10.0, "insider": True},           # insider -> excluded from concentration
            {"pct": 5.0, "tag": ""},                  # real
            {"pct": 5.0, "tag": ""},                  # real
        ],
    }
    f = compute_holder_features(rc)
    assert f["top10_holder_pct"] == 30.0          # 20 + 5 + 5 (LP + insider excluded)
    assert f["top1_holder_pct"] == 20.0
    assert f["top1_share_of_top10"] == round(20.0 / 30.0, 3)
    assert f["topholder_insider_n"] == 1          # the insider is still counted in its own signal


def test_dev_holder_pct_from_holders_fraction():
    rc = {
        "creator_address": "DEVabc",
        "holders": [{"account": "devABC", "percent": 0.07}],  # percent is 0..1 -> 7%
        "topHolders": [{"pct": 12.0, "tag": ""}],
    }
    f = compute_holder_features(rc)
    assert f["dev_holder_pct"] == 7.0


def test_lp_imbalance_dominant_pool():
    rc = {"markets": [
        {"lp": {"baseUSD": 1000.0, "quoteUSD": 100.0}},   # ratio 10x, depth 1100 (dominant)
        {"lp": {"baseUSD": 50.0, "quoteUSD": 50.0}},      # balanced but smaller depth
    ]}
    f = compute_holder_features(rc)
    assert f["lp_imbalance_ratio"] == 10.0
    assert f["lp_single_sided"] is True
    assert f["lp_dominant_depth_usd"] == 1100.0


def test_fail_soft_on_garbage():
    assert compute_holder_features(None) == {}
    assert compute_holder_features({}) == {}
    assert compute_holder_features({"topHolders": "not a list"}) == {}


if __name__ == "__main__":
    test_top_concentration_excludes_insiders_and_lp()
    test_dev_holder_pct_from_holders_fraction()
    test_lp_imbalance_dominant_pool()
    test_fail_soft_on_garbage()
    print("holder_features tests passed")
