"""stale_drift verdict — the age x pc_h24 bleed-cell shadow (2026-06-04).

OLD (>168h) x middle pc_h24 [-20,+60) = the validated bleed; fresh / deep-dip /
extension PASS (the win cells); missing features = NEUTRAL (fail-open)."""
import os
from core.stale_drift import stale_drift_verdict, age_threshold_hours, pc_band


def _m(age, pc):
    return {"lifecycle_age_hours": age, "pc_h24": pc}


# ── BLOCK: old x middle ──
def test_old_trough_blocks():
    assert stale_drift_verdict(_m(300, -5))[0] == "BLOCK"
    assert stale_drift_verdict(_m(200, 30))[0] == "BLOCK"   # old x mild


def test_block_boundaries():
    # just over the age line + inside the band -> block
    assert stale_drift_verdict(_m(168.1, 0))[0] == "BLOCK"
    assert stale_drift_verdict(_m(500, -19.9))[0] == "BLOCK"
    assert stale_drift_verdict(_m(500, 59.9))[0] == "BLOCK"


# ── PASS: the win cells (never de-sized) ──
def test_fresh_passes():
    assert stale_drift_verdict(_m(5, -5))[0] == "PASS"      # fresh trough = fine
    assert stale_drift_verdict(_m(10, 100))[0] == "PASS"    # fresh extension = the tail


def test_deep_dip_passes():
    assert stale_drift_verdict(_m(300, -40))[0] == "PASS"   # aged deep-dip = mean-reversion


def test_extension_passes():
    assert stale_drift_verdict(_m(300, 80))[0] == "PASS"    # extension (>=60) never blocked


def test_age_exactly_threshold_passes():
    # NOT strictly greater than threshold -> pass
    assert stale_drift_verdict(_m(168, 0))[0] == "PASS"


# ── NEUTRAL: fail-open on missing/bad features ──
def test_missing_age_neutral():
    assert stale_drift_verdict({"pc_h24": -5})[0] == "NEUTRAL"


def test_missing_pc_neutral():
    assert stale_drift_verdict({"lifecycle_age_hours": 300})[0] == "NEUTRAL"


def test_bool_not_numeric_neutral():
    assert stale_drift_verdict({"lifecycle_age_hours": True, "pc_h24": -5})[0] == "NEUTRAL"


# ── env overrides ──
def test_env_overrides(monkeypatch):
    monkeypatch.setenv("STALE_DRIFT_AGE_HRS", "72")
    monkeypatch.setenv("STALE_DRIFT_PC_LO", "-10")
    monkeypatch.setenv("STALE_DRIFT_PC_HI", "40")
    assert age_threshold_hours() == 72.0
    assert pc_band() == (-10.0, 40.0)
    assert stale_drift_verdict(_m(100, -5))[0] == "BLOCK"   # now old at 72h
    assert stale_drift_verdict(_m(100, 50))[0] == "PASS"    # 50 now outside [-10,40)
