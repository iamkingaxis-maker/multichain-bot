import pytest
from core.paper_fidelity import (
    reprice_entry,
    effective_fill,
    measured_live_slip_pct,
    paper_fee_usd,
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
