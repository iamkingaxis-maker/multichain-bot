"""ultra_platform_fee_pct — Ultra charges 0.5%/leg on <24h tokens (P10 fix)."""
from core.paper_fidelity import ultra_platform_fee_pct as upf


def test_young_token_pays_half_pct():
    assert upf(2.0) == 0.5
    assert upf(23.9) == 0.5


def test_aged_token_free():
    assert upf(24.0) == 0.0
    assert upf(500.0) == 0.0


def test_unknown_age_fails_open():
    assert upf(None) == 0.0
    assert upf(float("nan")) == 0.0
    assert upf("x") == 0.0
    assert upf(True) == 0.0


def test_env_off_switch(monkeypatch):
    monkeypatch.setenv("ULTRA_FEE_MODEL", "off")
    assert upf(2.0) == 0.0
