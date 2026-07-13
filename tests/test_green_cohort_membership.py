"""green_cohort_membership — SOL young-lane green ex-top-2 entry-cohort classifier
(2026-07-12 2-axis sweep). Measure-forward positive selector; returns the label of
the highest-edge green cohort a candidate is in ('' = none). Pure; never raises."""
from core.bot_evaluator import green_cohort_membership as gcm


def test_base_deep_liq():
    label, why = gcm(pc_h1=-50, liq=32000, bs_h1=1.2, unique_buyers_n=30)
    assert label == "base"
    assert "deep" in why


def test_cohort_a_liq_bsh1():
    # not deep, not enough buyers, but liq>=45k & bs_h1>=1.6
    label, why = gcm(pc_h1=-20, liq=46000, bs_h1=1.7, unique_buyers_n=40)
    assert label == "liq_bsh1"
    assert "bs_h1" in why


def test_cohort_b_liq_ubuy():
    label, why = gcm(pc_h1=-20, liq=36000, bs_h1=1.2, unique_buyers_n=52)
    assert label == "liq_ubuy"
    assert "unique_buyers" in why


def test_no_cohort():
    label, why = gcm(pc_h1=-10, liq=28000, bs_h1=1.1, unique_buyers_n=20)
    assert label == ""
    assert "no green cohort" in why


def test_highest_edge_wins_on_overlap():
    # qualifies for base AND liq_bsh1 AND liq_ubuy -> base (highest edge) wins
    label, why = gcm(pc_h1=-50, liq=50000, bs_h1=2.0, unique_buyers_n=60)
    assert label == "base"
    assert "+2 more" in why


def test_boundaries_inclusive():
    assert gcm(-45.0, 30000.0, 1.0, 0)[0] == "base"       # exactly at base thresholds
    assert gcm(-44.9, 29999.0, 1.0, 0)[0] == ""           # just outside base
    assert gcm(0, 45000.0, 1.6, 0)[0] == "liq_bsh1"       # exactly at A thresholds
    assert gcm(0, 44999.0, 1.6, 0)[0] == ""               # liq just under A
    assert gcm(0, 35000.0, 0, 50.0)[0] == "liq_ubuy"      # exactly at B thresholds
    assert gcm(0, 35000.0, 0, 49.0)[0] == ""              # ubuy just under B


def test_missing_and_garbage_unclassified():
    assert gcm(None, None, None, None)[0] == ""
    assert gcm(float("nan"), 50000.0, float("nan"), 60)[0] == "liq_ubuy"  # liq+ubuy still valid
    assert gcm(True, "x", None, 10)[0] == ""              # bool/str coerced to absent
    # a partially-missing candidate never raises
    label, _ = gcm(-50, None, None, None)
    assert label == ""  # base needs liq too


def test_env_threshold_override(monkeypatch):
    monkeypatch.setenv("GREEN_COHORT_A_BSH1_MIN", "2.5")
    # bs_h1=1.7 no longer clears the raised A bar
    assert gcm(-20, 46000, 1.7, 40)[0] == ""
    assert gcm(-20, 46000, 2.6, 40)[0] == "liq_bsh1"
