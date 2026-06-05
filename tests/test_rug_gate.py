"""rug_gate verdict + compute_holder_features LP-lock/burn/score extraction (2026-06-04)."""
from core.rug_gate import rug_gate_verdict, lp_lock_min_pct
from core.holder_features import compute_holder_features

SYS = "11111111111111111111111111111111"  # system address => burned LP


# ── rug_gate_verdict ──
def test_burned_passes():
    assert rug_gate_verdict({"lp_burned": True, "lp_locked_pct": 0.0})[0] == "PASS"  # burn > lock


def test_unlocked_not_burned_blocks():
    assert rug_gate_verdict({"lp_locked_pct": 0.0, "lp_burned": False})[0] == "BLOCK"


def test_locked_passes():
    assert rug_gate_verdict({"lp_locked_pct": 95.0, "lp_burned": False})[0] == "PASS"


def test_unknown_fails_open():
    assert rug_gate_verdict({})[0] == "NEUTRAL"
    assert rug_gate_verdict({"lp_locked_pct": None})[0] == "NEUTRAL"
    assert rug_gate_verdict({"lp_locked_pct": True})[0] == "NEUTRAL"


def test_threshold_env(monkeypatch):
    monkeypatch.setenv("RUG_GATE_LP_LOCK_MIN", "50")
    assert lp_lock_min_pct() == 50.0
    assert rug_gate_verdict({"lp_locked_pct": 40, "lp_burned": False})[0] == "BLOCK"
    assert rug_gate_verdict({"lp_locked_pct": 60, "lp_burned": False})[0] == "PASS"


# ── compute_holder_features LP extraction ──
def test_burn_override_sets_100():
    rc = {"markets": [{"mintLP": SYS, "lp": {"baseUSD": 100, "quoteUSD": 5000}}]}
    f = compute_holder_features(rc)
    assert f["lp_locked_pct"] == 100.0 and f["lp_burned"] is True


def test_dominant_pool_lplocked_extracted():
    rc = {"markets": [
        {"mintLP": "Xabc", "lp": {"baseUSD": 10, "quoteUSD": 100, "lpLockedPct": 12.0}},   # small
        {"mintLP": "Ydef", "lp": {"baseUSD": 100, "quoteUSD": 9000, "lpLockedPct": 88.0}},  # dominant
    ]}
    f = compute_holder_features(rc)
    assert f["lp_locked_pct"] == 88.0 and f["lp_burned"] is False  # dominant pool's value


def test_toplevel_lplocked_fallback():
    rc = {"lpLockedPct": 73.0, "markets": [{"mintLP": "Z", "lp": {"baseUSD": 50, "quoteUSD": 500}}]}
    f = compute_holder_features(rc)
    assert f["lp_locked_pct"] == 73.0


def test_score_extracted():
    assert compute_holder_features({"score_normalised": 42.5})["rugcheck_score"] == 42.5
    assert compute_holder_features({"score": 17})["rugcheck_score"] == 17.0


def test_no_markets_no_lp_field():
    f = compute_holder_features({"topHolders": []})
    assert "lp_locked_pct" not in f  # absent -> rug_gate fails open


def test_endtoend_burned_token_passes_gate():
    rc = {"markets": [{"mintLP": SYS, "lp": {"baseUSD": 100, "quoteUSD": 5000}}]}
    assert rug_gate_verdict(compute_holder_features(rc))[0] == "PASS"


def test_endtoend_unlocked_token_blocks():
    rc = {"markets": [{"mintLP": "realmint", "lp": {"baseUSD": 100, "quoteUSD": 5000, "lpLockedPct": 0.0}}]}
    assert rug_gate_verdict(compute_holder_features(rc))[0] == "BLOCK"
