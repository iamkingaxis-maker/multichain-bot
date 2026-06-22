import pytest
from core.paper_fidelity import (
    reprice_entry,
    effective_fill,
    measured_live_slip_pct,
    paper_fee_usd,
    no_route_skip,
    slippage_cap_skip,
)

def test_fresh_price_used_as_entry_on_dip():
    # fresh below decision (further dip) -> use fresh
    assert reprice_entry(0.10, 0.09)[0] == 0.09

def test_fresh_price_used_on_subthreshold_runup():
    # fresh slightly above, within max_runup -> use fresh (reachable)
    eb, why = reprice_entry(0.10, 0.104, max_runup=0.05)
    assert eb == 0.104

def test_runup_past_threshold_aborts_to_mirror_live():
    eb, why = reprice_entry(0.10, 0.20, max_runup=0.05)  # +100% runup
    assert eb is None and why == "runup_abort"

def test_missing_fresh_falls_back_to_decision():
    assert reprice_entry(0.10, None)[0] == 0.10
    assert reprice_entry(0.10, 0.0)[0] == 0.10

def test_buy_pays_up_slip_and_fee():
    # mid 0.10, 1.5% slip, $0.17 fee on $100 = 0.17% -> ~1.67% pay-up
    f = effective_fill(0.10, "buy", slip_pct=1.5, fee_usd=0.17, size_usd=100)
    assert abs(f - 0.10*(1+0.0167)) < 1e-9

def test_sell_receives_less_slip_and_fee():
    f = effective_fill(0.10, "sell", slip_pct=1.5, fee_usd=0.17, size_usd=100)
    assert abs(f - 0.10*(1-0.0167)) < 1e-9

def test_defaults():
    assert measured_live_slip_pct() == 1.5
    assert paper_fee_usd() == 0.17

def test_no_route_skip_when_no_fresh_price():
    assert no_route_skip(fresh_source="none", mode="enforce") is True
    assert no_route_skip(fresh_source="onchain", mode="enforce") is False
    assert no_route_skip(fresh_source="none", mode="off") is False  # gate off

def test_slippage_cap_skip():
    assert slippage_cap_skip(5.0, cap_pct=4.0) is True
    assert slippage_cap_skip(2.0, cap_pct=4.0) is False
    assert slippage_cap_skip(None) is False  # fail-open

def test_no_route_skip_shadow_mode():
    assert no_route_skip(fresh_source="none", mode="shadow") is True

def test_no_route_skip_fail_open_missing_source():
    assert no_route_skip(fresh_source=None, mode="enforce") is False

def test_slippage_cap_default_from_env(monkeypatch):
    monkeypatch.setenv("PROBE_ULTRA_SLIPPAGE_BPS", "100")  # /100 = 1.0%
    assert slippage_cap_skip(1.5) is True
    assert slippage_cap_skip(0.5) is False

def test_slippage_cap_default_no_env(monkeypatch):
    monkeypatch.delenv("PROBE_ULTRA_SLIPPAGE_BPS", raising=False)
    # default 400 bps -> 4.0%
    assert slippage_cap_skip(4.5) is True
    assert slippage_cap_skip(3.5) is False
