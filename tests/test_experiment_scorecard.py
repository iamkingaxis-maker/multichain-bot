"""Tests for the pure ex-top-2 / verdict logic of scripts/experiment_scorecard.py.

The scorecard drives PROMOTE/RETIRE decisions, so the metric that produces them
must be locked: per-token median, drop each cohort's 2 BEST tokens, median of the
rest; GREEN = ex2>0 AND >=50% tokens green; lifetime sum is never the verdict.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.experiment_scorecard import (  # noqa: E402
    ex_top2, per_token_medians, verdict)


def _trips(pairs):
    """pairs: list of (token, ret) -> trip dicts."""
    return [{"token": tok, "ret": ret} for tok, ret in pairs]


# ── per_token_medians ─────────────────────────────────────────────────────

def test_per_token_median_collapses_legs():
    pt = per_token_medians(_trips([("A", 10), ("A", 20), ("A", 30), ("B", -5)]))
    assert pt == {"A": 20.0, "B": -5.0}


def test_per_token_ignores_missing_ret():
    trips = [{"token": "A", "ret": 5}, {"token": "A", "ret": None},
             {"token": "B", "ret": "x"}]
    assert per_token_medians(trips) == {"A": 5.0}


# ── ex_top2: the honest metric ────────────────────────────────────────────

def test_ex_top2_drops_two_best_tokens():
    # tokens medians: A=+100, B=+90, C=-2, D=-4, E=-6. Drop A,B -> median(-2,-4,-6)=-4.
    m = ex_top2(_trips([("A", 100), ("B", 90), ("C", -2), ("D", -4), ("E", -6)]))
    assert m["n_tokens"] == 5
    assert m["ex2_median"] == -4.0
    # plain median (no drop) would be -2 — the fat-tail drop is stricter.
    assert m["plain_median"] == -2.0


def test_ex_top2_pct_green_over_all_tokens():
    # 3 of 5 tokens green -> 60%.
    m = ex_top2(_trips([("A", 5), ("B", 3), ("C", 1), ("D", -4), ("E", -6)]))
    assert m["pct_green"] == 60.0


def test_ex_top2_fat_tail_cannot_carry_median():
    # One monster winner + many losers: dropping the 2 best keeps it red.
    trips = _trips([("W", 500)] + [(f"L{i}", -8) for i in range(6)])
    m = ex_top2(trips)
    assert m["ex2_median"] < 0        # sum would be hugely +, verdict is not


def test_ex_top2_small_cohort_no_drop():
    # <=2 tokens: nothing to drop, still returns a median (never crashes).
    m = ex_top2(_trips([("A", 4), ("B", -2)]))
    assert m["n_tokens"] == 2
    assert m["ex2_median"] == 1.0


def test_ex_top2_empty():
    m = ex_top2([])
    assert m == {"n_tokens": 0, "ex2_median": None, "pct_green": None,
                 "plain_median": None}


# ── verdict mapping ───────────────────────────────────────────────────────

def test_verdict_no_data():
    assert verdict({"n_tokens": 0, "ex2_median": None, "pct_green": None}, 20) \
        == "NO-DATA"


def test_verdict_accruing_below_bar():
    assert verdict({"n_tokens": 10, "ex2_median": 5.0, "pct_green": 80.0}, 20) \
        == "ACCRUING"


def test_verdict_promote_bar_met():
    assert verdict({"n_tokens": 22, "ex2_median": 2.7, "pct_green": 64.0}, 20) \
        == "PROMOTE"


def test_verdict_retire_clear_fail():
    assert verdict({"n_tokens": 40, "ex2_median": -5.8, "pct_green": 36.0}, 30) \
        == "RETIRE"


def test_verdict_mixed_one_criterion():
    # green median but <50% tokens green -> borderline, not a clean promote.
    assert verdict({"n_tokens": 30, "ex2_median": 1.0, "pct_green": 40.0}, 30) \
        == "MIXED"
    # >=50% green but red median -> also mixed.
    assert verdict({"n_tokens": 30, "ex2_median": -0.5, "pct_green": 55.0}, 30) \
        == "MIXED"


def test_verdict_promote_needs_both_at_exact_bar():
    # n exactly at bar counts as met.
    assert verdict({"n_tokens": 20, "ex2_median": 0.1, "pct_green": 50.0}, 20) \
        == "PROMOTE"


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(
        [sys.executable, "-m", "pytest", __file__, "-q"]))
