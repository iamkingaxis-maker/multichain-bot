"""Tests for the full-thesis cohort entry gate (coverage audit 2026-06-29).

A profitable badday dip = a GENUINE 6h decliner (pc_h6 <= 0, not a pump-retrace)
MET BY real buyer size (median_buy_size_usd >= ~34.3, reusing the validated winner
selector). ENFORCE blocks ONLY a CONFIRMED out-of-cohort candidate; it FAILS OPEN
on any missing/NaN signal (the median_buy_size_usd FeatureBundle gap on fast-watch
entries must NOT dark the fleet). 3-state: (selected, blocked, why).
"""
from core.bot_evaluator import full_thesis_cohort_eval as ftc


def test_selected_when_decliner_and_buyers():
    selected, blocked, why = ftc(-3.0, 41.0)
    assert selected is True
    assert blocked is False
    assert "pc_h6" in why and "buyer" in why


def test_block_when_pump_retrace():
    selected, blocked, why = ftc(5.0, 41.0)
    assert selected is False
    assert blocked is True
    assert "pc_h6" in why


def test_block_when_low_buyer():
    selected, blocked, why = ftc(-3.0, 12.0)
    assert selected is False
    assert blocked is True
    assert "buyer" in why


def test_block_when_both_fail():
    # pump-retrace AND low buyer -> still a single confirmed block
    selected, blocked, why = ftc(5.0, 12.0)
    assert selected is False
    assert blocked is True
    assert "pc_h6" in why and "buyer" in why


def test_failopen_missing():
    # Any missing/NaN/garbage signal -> NEVER blocked (fail-open), never raises.
    for h6, buyer in [
        (None, 41.0),       # decline missing
        (-3.0, None),       # buyer missing
        (None, None),       # both missing
        (float("nan"), 41.0),
        (-3.0, float("nan")),
        ("garbage", 41.0),
        (-3.0, "garbage"),
    ]:
        selected, blocked, why = ftc(h6, buyer)
        assert blocked is False, f"must fail-open (no block) for {(h6, buyer)}"
        assert selected is False, f"cannot be selected when a signal is missing {(h6, buyer)}"
        assert "unknown" in why.lower()


def test_failopen_decline_ok_buyer_missing():
    # decline side passes but buyer absent -> unknown-allow, not selected, not blocked
    selected, blocked, why = ftc(-3.0, None)
    assert (selected, blocked) == (False, False)
    assert "unknown" in why.lower()


def test_threshold_env(monkeypatch):
    monkeypatch.setenv("WINNER_SIZE_MEDIAN_BUY_USD", "100")
    # 50 buyer now below the raised cutoff -> confirmed low-buyer block
    selected, blocked, why = ftc(-3.0, 50.0)
    assert selected is False
    assert blocked is True
    assert "buyer" in why
    # 120 buyer above raised cutoff -> selected
    selected2, blocked2, _ = ftc(-3.0, 120.0)
    assert selected2 is True
    assert blocked2 is False


def test_threshold_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("WINNER_SIZE_MEDIAN_BUY_USD", "100")
    selected, blocked, _ = ftc(-3.0, 50.0, buyer_threshold=34.3)
    assert selected is True
    assert blocked is False


def test_boundary():
    # pc_h6 == 0.0 -> decline side TRUE (<= 0). buyer EXACTLY at threshold ->
    # selected (mirror winner_demand_selected inclusive boundary v >= thr).
    selected, blocked, why = ftc(0.0, 34.3)
    assert selected is True
    assert blocked is False
    # pc_h6 slightly positive -> pump-retrace block
    selected2, blocked2, _ = ftc(0.1, 34.3)
    assert selected2 is False
    assert blocked2 is True


def test_never_raises_on_weird_input():
    # bool / object inputs must not raise and must fail-open
    for h6, buyer in [(True, 41.0), (-3.0, True), (object(), 41.0)]:
        selected, blocked, _ = ftc(h6, buyer)
        assert blocked is False
