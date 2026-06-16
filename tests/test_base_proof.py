# -*- coding: utf-8 -*-
"""chameleon_base_proof (2026-06-16) — the conservative SHADOW would-swap logger for the
CHAMELEON_STATIC_BASE incumbent. A challenger earns a WOULD-SWAP log only on rigorous,
time-separated proof: beats the incumbent by >= EDGE_MIN per-day on >= MIN_DAYS distinct days
where BOTH cleared MIN_DAY_N trades, pooled n >= MIN_POOLED_N. Never a trailing-best chase."""
import time
from core import chameleon_base_proof as bp


def _set(rollup):
    bp._rollup = rollup
    bp._last_save = time.time()   # skip the file-persist side effect in tests
    bp._last_log_ts = 0.0


def test_check_requires_two_n30_days_with_edge():
    _set({
        "badday_flush": {"d1": [30.0, 30], "d2": [30.0, 30]},   # incumbent mean +1.0/day, n30
        "good_chal":    {"d1": [60.0, 30], "d2": [60.0, 30]},   # +2.0/day (beats by +1.0), pooled 60
        "oneday_chal":  {"d1": [90.0, 30]},                     # beats but only ONE day
        "lown_chal":    {"d1": [200.0, 10], "d2": [200.0, 10]},  # beats but n<30/day
        "tie_chal":     {"d1": [33.0, 30], "d2": [33.0, 30]},   # +1.1 vs +1.0 = +0.1 < EDGE_MIN 0.5
    })
    chals = {f["challenger"] for f in bp.check("badday_flush")}
    assert "good_chal" in chals
    assert "oneday_chal" not in chals    # <MIN_DAYS
    assert "lown_chal" not in chals      # n<MIN_DAY_N
    assert "tie_chal" not in chals       # edge < EDGE_MIN


def test_check_respects_eligible_filter():
    _set({
        "badday_flush": {"d1": [30.0, 30], "d2": [30.0, 30]},
        "good_chal":    {"d1": [60.0, 30], "d2": [60.0, 30]},
    })
    out = bp.check("badday_flush", eligible=lambda b: b != "good_chal")
    assert all(f["challenger"] != "good_chal" for f in out)


def test_check_no_incumbent_data_returns_empty():
    _set({"some_bot": {"d1": [60.0, 30]}})
    assert bp.check("badday_flush") == []


def test_record_drops_phantom_and_accumulates():
    _set({})
    bp.record("x", 5000.0, ts=1000.0)   # phantom |>300| dropped
    bp.record("x", 5.0, ts=1000.0)
    bp.record("x", 3.0, ts=1000.0)
    day = list(bp._rollup["x"])[0]
    assert bp._rollup["x"][day] == [8.0, 2]   # two clean legs, phantom excluded
