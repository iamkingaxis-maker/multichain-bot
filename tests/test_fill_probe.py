"""Tests for the quote-based fill-accuracy shadow probe (core/fill_probe.py).

Covers the PURE compute_fill_probe math, the regression case (paper modeled a
cheap fill but the real Jupiter impact is large -> model_error strongly
negative = paper too optimistic), fail-open on garbage, and the read-side
summary used by GET /api/fill-probe.
"""
import math

from core.fill_probe import compute_fill_probe, summarize_fill_probes


def test_compute_exact_known_inputs():
    # decision_mid=100, fresh moved up to 102 (drift +2%), real impact +3% on
    # the fresh price -> real_fill = 102 * 1.03 = 105.06.
    out = compute_fill_probe(
        decision_mid=100.0,
        fresh_price=102.0,
        real_impact_pct=3.0,
        paper_modeled_fill=101.5,  # paper booked +1.5% vs decision
        size_usd=30.0,
        liquidity_usd=50000.0,
    )
    assert out["decision_mid"] == 100.0
    assert out["fresh_price"] == 102.0
    assert out["real_impact_pct"] == 3.0
    assert math.isclose(out["real_fill_price"], 105.06, rel_tol=1e-9)
    assert math.isclose(out["real_drift_pct"], 2.0, rel_tol=1e-9)
    # real_total_cost = (105.06/100 - 1)*100 = 5.06%
    assert math.isclose(out["real_total_cost_pct"], 5.06, rel_tol=1e-9)
    # paper_total_cost = (101.5/100 - 1)*100 = 1.5%
    assert math.isclose(out["paper_total_cost_pct"], 1.5, rel_tol=1e-9)
    # model_error = 1.5 - 5.06 = -3.56 (paper too optimistic/cheap)
    assert math.isclose(out["model_error_pct"], -3.56, rel_tol=1e-9)
    assert out["size_usd"] == 30.0
    assert out["liquidity_usd"] == 50000.0


def test_regression_paper_too_optimistic():
    # The headline regression: paper modeled -1.5% (a cheap, favorable fill) but
    # the real on-chain impact is +6% with no drift -> model_error strongly
    # negative = paper materially too optimistic.
    out = compute_fill_probe(
        decision_mid=1.0,
        fresh_price=1.0,            # no drift
        real_impact_pct=6.0,
        paper_modeled_fill=0.985,   # -1.5% vs decision
        size_usd=500.0,
        liquidity_usd=20000.0,
    )
    assert math.isclose(out["real_total_cost_pct"], 6.0, rel_tol=1e-9)
    assert math.isclose(out["paper_total_cost_pct"], -1.5, rel_tol=1e-9)
    # model_error = -1.5 - 6.0 = -7.5  (<0 => paper too optimistic)
    assert math.isclose(out["model_error_pct"], -7.5, rel_tol=1e-9)
    assert out["model_error_pct"] < 0


def test_paper_too_pessimistic_positive_error():
    # Paper booked +5% but real cost only +1% -> model_error positive.
    out = compute_fill_probe(
        decision_mid=10.0,
        fresh_price=10.0,
        real_impact_pct=1.0,
        paper_modeled_fill=10.5,
        size_usd=30.0,
        liquidity_usd=120000.0,
    )
    assert math.isclose(out["paper_total_cost_pct"], 5.0, rel_tol=1e-9)
    assert math.isclose(out["real_total_cost_pct"], 1.0, rel_tol=1e-9)
    assert math.isclose(out["model_error_pct"], 4.0, rel_tol=1e-9)
    assert out["model_error_pct"] > 0


def test_fail_open_zero_decision_mid():
    assert compute_fill_probe(0.0, 1.0, 1.0, 1.0, 30.0, 50000.0) == {}


def test_fail_open_garbage():
    assert compute_fill_probe("x", None, float("nan"), {}, [], None) == {}
    assert compute_fill_probe(None, None, None, None, None, None) == {}


def test_fail_open_nan_impact():
    assert compute_fill_probe(1.0, 1.0, float("nan"), 1.0, 30.0, 50000.0) == {}


def test_summarize_empty():
    s = summarize_fill_probes([])
    assert s["n"] == 0
    assert s["median_model_error_pct"] is None
    assert s["frac_abs_error_gt_2"] is None
    assert s["by_liquidity_bucket"] == {}


def test_summarize_buckets_and_key_metric():
    recs = [
        # thin bucket (<30k): two records, one with |error|>2
        {"real_impact_pct": 6.0, "real_total_cost_pct": 6.0, "real_drift_pct": 0.0,
         "model_error_pct": -7.5, "liquidity_usd": 20000.0},
        {"real_impact_pct": 1.0, "real_total_cost_pct": 1.0, "real_drift_pct": 0.0,
         "model_error_pct": -0.5, "liquidity_usd": 10000.0},
        # mid bucket (30-100k)
        {"real_impact_pct": 2.0, "real_total_cost_pct": 2.5, "real_drift_pct": 0.5,
         "model_error_pct": 1.0, "liquidity_usd": 50000.0},
        # deep bucket (100k+)
        {"real_impact_pct": 0.5, "real_total_cost_pct": 0.6, "real_drift_pct": 0.1,
         "model_error_pct": 3.0, "liquidity_usd": 150000.0},
    ]
    s = summarize_fill_probes(recs)
    assert s["n"] == 4
    # 2 of 4 have |model_error_pct| > 2  (-7.5 and 3.0)
    assert math.isclose(s["frac_abs_error_gt_2"], 0.5, rel_tol=1e-9)
    assert s["median_model_error_pct"] is not None
    b = s["by_liquidity_bucket"]
    assert b["thin"]["n"] == 2
    assert b["mid"]["n"] == 1
    assert b["deep"]["n"] == 1
    # thin bucket key metric present
    assert b["thin"]["median_model_error_pct"] is not None


def test_summarize_ignores_nonnumeric():
    recs = [
        {"model_error_pct": "bad", "liquidity_usd": 50000.0},
        {"model_error_pct": -3.0, "liquidity_usd": 50000.0},
    ]
    s = summarize_fill_probes(recs)
    assert s["n"] == 2  # n counts records
    # only one numeric model_error -> still summarizable
    assert s["median_model_error_pct"] == -3.0
