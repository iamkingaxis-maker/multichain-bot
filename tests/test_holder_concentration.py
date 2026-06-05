"""holder_concentration verdict — extreme-top10 rug-proxy shadow (2026-06-04)."""
from core.holder_concentration import holder_concentration_verdict, top10_threshold


def test_extreme_blocks():
    assert holder_concentration_verdict({"top10_holder_pct": 91.2})[0] == "BLOCK"   # APM-type
    assert holder_concentration_verdict({"top10_holder_pct": 90.0})[0] == "BLOCK"   # boundary inclusive


def test_below_threshold_passes():
    assert holder_concentration_verdict({"top10_holder_pct": 55.9})[0] == "PASS"    # grail (moderate)
    assert holder_concentration_verdict({"top10_holder_pct": 13.9})[0] == "PASS"    # SPARQ (low conc.)
    assert holder_concentration_verdict({"top10_holder_pct": 89.9})[0] == "PASS"


def test_missing_neutral():
    assert holder_concentration_verdict({})[0] == "NEUTRAL"
    assert holder_concentration_verdict({"top10_holder_pct": None})[0] == "NEUTRAL"
    assert holder_concentration_verdict({"top10_holder_pct": True})[0] == "NEUTRAL"


def test_env_override(monkeypatch):
    monkeypatch.setenv("HOLDER_CONC_TOP10_THR", "80")
    assert top10_threshold() == 80.0
    assert holder_concentration_verdict({"top10_holder_pct": 82})[0] == "BLOCK"
    assert holder_concentration_verdict({"top10_holder_pct": 78})[0] == "PASS"
