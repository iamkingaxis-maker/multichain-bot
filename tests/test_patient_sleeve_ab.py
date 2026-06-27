"""A/B compare: patient_sleeve vs the time-box fleet on shared tokens (2026-06-26)."""
from scripts.patient_sleeve_ab import compare_arms


def test_compare_pairs_same_token_and_reports_means():
    recs = [
        {"bot_id": "patient_sleeve", "address": "A", "fully_closed": True, "pnl_pct": 40.0},
        {"bot_id": "badday_flush", "address": "A", "fully_closed": True, "pnl_pct": 3.0},
        {"bot_id": "patient_sleeve", "address": "B", "fully_closed": True, "pnl_pct": -22.0},
        {"bot_id": "badday_flush", "address": "B", "fully_closed": True, "pnl_pct": -6.0},
    ]
    out = compare_arms(recs)
    assert out["paired_tokens"] == 2
    assert round(out["patient_mean"], 1) == 9.0
    assert round(out["timebox_mean"], 1) == -1.5


def test_multileg_blended_fraction_weighted():
    # patient partial-TP-then-stop: 0.25@+15, 0.25@+40, 0.5@-22 = +2.75 blended (NOT -22)
    recs = [
        {"bot_id": "patient_sleeve", "address": "A", "entry_price": 1.0, "kind": "TP1",
         "fully_closed": False, "pnl_pct": 15.0, "sell_fraction": 0.25},
        {"bot_id": "patient_sleeve", "address": "A", "entry_price": 1.0, "kind": "TP2",
         "fully_closed": False, "pnl_pct": 40.0, "sell_fraction": 0.25},
        {"bot_id": "patient_sleeve", "address": "A", "entry_price": 1.0, "kind": "HARD_STOP",
         "fully_closed": True, "pnl_pct": -22.0, "sell_fraction": 0.5},
        {"bot_id": "badday_flush", "address": "A", "entry_price": 1.0,
         "fully_closed": True, "pnl_pct": -6.0, "sell_fraction": 1.0},
    ]
    out = compare_arms(recs)
    assert out["paired_tokens"] == 1
    assert round(out["patient_mean"], 2) == 2.75   # fraction-weighted, not the -22 final leg


def test_only_paired_tokens_counted():
    # token C is patient-only (no time-box arm) -> excluded from the paired comparison
    recs = [
        {"bot_id": "patient_sleeve", "address": "A", "fully_closed": True, "pnl_pct": 10.0},
        {"bot_id": "badday_flush", "address": "A", "fully_closed": True, "pnl_pct": 2.0},
        {"bot_id": "patient_sleeve", "address": "C", "fully_closed": True, "pnl_pct": 99.0},
    ]
    out = compare_arms(recs)
    assert out["paired_tokens"] == 1


def test_tail_rate_counts_big_winners():
    recs = [
        {"bot_id": "patient_sleeve", "address": "A", "fully_closed": True, "pnl_pct": 30.0},
        {"bot_id": "badday_flush", "address": "A", "fully_closed": True, "pnl_pct": 5.0},
    ]
    out = compare_arms(recs)
    assert out["patient_tail_rate"] == 1.0   # 30% > +25%
    assert out["timebox_tail_rate"] == 0.0


def test_ignores_open_and_nonnumeric():
    recs = [
        {"bot_id": "patient_sleeve", "address": "A", "fully_closed": False, "pnl_pct": 40.0},
        {"bot_id": "badday_flush", "address": "A", "fully_closed": True, "pnl_pct": 3.0},
    ]
    out = compare_arms(recs)
    assert out["paired_tokens"] == 0   # patient leg not closed -> no pair
