import pytest
from core.paper_fidelity import (
    reprice_entry,
    effective_fill,
    measured_live_slip_pct,
    paper_fee_usd,
    no_route_skip,
    slippage_cap_skip,
    gap_through_extra_pct,
    paper_entry_decision,
    paper_exit_decision,
    caps_would_block,
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

def test_no_route_skip_jupiter_is_a_route():
    # a jupiter price IS a reachable route -> never skip (the default-config path)
    assert no_route_skip(fresh_source="jupiter", mode="enforce") is False
    assert no_route_skip(fresh_source="jupiter", mode="shadow") is False

def test_no_route_skip_unknown_source_is_no_route():
    assert no_route_skip(fresh_source="", mode="enforce") is True
    assert no_route_skip(fresh_source="something_else", mode="enforce") is True

def test_slippage_cap_skip():
    assert slippage_cap_skip(5.0, cap_pct=4.0) is True
    assert slippage_cap_skip(2.0, cap_pct=4.0) is False
    assert slippage_cap_skip(None) is False  # fail-open

def test_no_route_skip_shadow_mode():
    assert no_route_skip(fresh_source="none", mode="shadow") is True

def test_no_route_skip_none_source_is_no_route_when_gated():
    # None source = no fresh price at all -> skip when the gate is armed
    assert no_route_skip(fresh_source=None, mode="enforce") is True
    assert no_route_skip(fresh_source=None, mode="off") is False  # gate off

def test_slippage_cap_default_from_env(monkeypatch):
    monkeypatch.setenv("PROBE_ULTRA_SLIPPAGE_BPS", "100")  # /100 = 1.0%
    assert slippage_cap_skip(1.5) is True
    assert slippage_cap_skip(0.5) is False

def test_slippage_cap_default_no_env(monkeypatch):
    monkeypatch.delenv("PROBE_ULTRA_SLIPPAGE_BPS", raising=False)
    # default 400 bps -> 4.0%
    assert slippage_cap_skip(4.5) is True
    assert slippage_cap_skip(3.5) is False

def test_hard_stop_gaps():
    assert gap_through_extra_pct("HARD_STOP pnl=-25%") == 5.0

def test_tp_does_not_gap():
    assert gap_through_extra_pct("TP1 pnl=6.0%") == 0.0

def test_none_safe():
    assert gap_through_extra_pct(None) == 0.0

def test_gap_matches_fast_bail_and_giveback():
    assert gap_through_extra_pct("FAST_BAIL pnl=-10%") == 5.0
    assert gap_through_extra_pct("giveback trail") == 5.0

def test_gap_matches_generic_stop_substring():
    assert gap_through_extra_pct("trail-stop hit") == 5.0
    assert gap_through_extra_pct("never_runner_stop") == 5.0

def test_gap_garbage_reason_fail_open():
    assert gap_through_extra_pct(12345) == 0.0
    assert gap_through_extra_pct("") == 0.0
    assert gap_through_extra_pct("manual_close") == 0.0

def test_gap_haircut_from_env(monkeypatch):
    monkeypatch.setenv("GAP_THROUGH_HAIRCUT_PCT", "8.0")
    assert gap_through_extra_pct("HARD_STOP") == 8.0
    assert gap_through_extra_pct("TP1") == 0.0

def test_gap_bad_env_fails_open_to_default(monkeypatch):
    monkeypatch.setenv("GAP_THROUGH_HAIRCUT_PCT", "notanumber")
    assert gap_through_extra_pct("HARD_STOP") == 5.0

# --- paper_entry_decision (composition) ---

def test_paper_entry_off_returns_mid_unchanged():
    eb, why = paper_entry_decision(0.10, 0.09, "onchain", 1.0, "off", 100)
    assert eb == 0.10 and why == "off"

def test_paper_entry_fresh_used_with_slip_and_fee():
    # mode shadow/enforce, fresh below stale -> reprice to 0.09, then buy pay-up
    eb, why = paper_entry_decision(0.10, 0.09, "onchain", 1.0, "enforce", 100,
                                   slip_pct=1.5, fee_usd=0.17, max_runup=0.05)
    expected = 0.09 * (1 + 0.015 + 0.17/100)
    assert why == "fresh" and abs(eb - expected) < 1e-9

def test_paper_entry_runup_skips():
    eb, why = paper_entry_decision(0.10, 0.20, "onchain", 1.0, "enforce", 100,
                                   max_runup=0.05)
    assert eb is None and why == "runup_abort"

def test_paper_entry_no_route_skips():
    eb, why = paper_entry_decision(0.10, 0.09, "none", 1.0, "enforce", 100)
    assert eb is None and why == "no_route"

def test_paper_entry_jupiter_source_does_not_skip():
    # jupiter is a reachable route -> entry books normally (default-config path)
    eb, why = paper_entry_decision(0.10, 0.09, "jupiter", 1.0, "enforce", 100,
                                   slip_pct=1.5, fee_usd=0.17, max_runup=0.05)
    expected = 0.09 * (1 + 0.015 + 0.17/100)
    assert why == "fresh" and abs(eb - expected) < 1e-9

def test_paper_entry_slippage_cap_skips():
    eb, why = paper_entry_decision(0.10, 0.09, "onchain", 9.0, "enforce", 100,
                                   slip_pct=1.5, fee_usd=0.17)
    assert eb is None and why == "slippage_cap"

def test_paper_entry_defaults_slip_fee_when_none():
    eb, why = paper_entry_decision(0.10, 0.09, "onchain", 1.0, "shadow", 100)
    expected = 0.09 * (1 + measured_live_slip_pct()/100 + paper_fee_usd()/100)
    assert why == "fresh" and abs(eb - expected) < 1e-9

def test_paper_entry_fail_open_on_garbage():
    # garbage size that would blow up -> fail-open returns mid
    eb, why = paper_entry_decision(0.10, 0.09, "onchain", 1.0, "enforce", "bad")
    # effective_fill is itself fail-open (fee_frac->0), so still computes;
    # force a real exception via a non-numeric mode path is covered by off.
    assert eb is not None  # never raises into buy path

# --- paper_exit_decision (composition, SELL side) ---

def test_paper_exit_hard_stop_gets_gap_haircut():
    # fresh reprice -> sell receives less (slip+fee) -> THEN gap haircut on a stop
    eb, why = paper_exit_decision(0.10, 0.095, "HARD_STOP pnl=-25%", "enforce", 100,
                                  slip_pct=1.5, fee_usd=0.17)
    base = 0.095 * (1 - 0.015 - 0.17/100)  # effective sell fill on fresh
    expected = base * (1 - 5.0/100)        # gap-through haircut
    assert why == "fresh" and abs(eb - expected) < 1e-9

def test_paper_exit_tp1_no_gap_haircut():
    eb, why = paper_exit_decision(0.10, 0.095, "TP1 pnl=6.0%", "enforce", 100,
                                  slip_pct=1.5, fee_usd=0.17)
    expected = 0.095 * (1 - 0.015 - 0.17/100)  # no gap haircut for a TP
    assert why == "fresh" and abs(eb - expected) < 1e-9

def test_paper_exit_off_returns_mid_unchanged():
    eb, why = paper_exit_decision(0.10, 0.095, "HARD_STOP", "off", 100)
    assert eb == 0.10 and why == "off"

def test_paper_exit_stale_fresh_falls_back_to_decision_mid():
    # no reachable fresh price -> reprice to decision_mid (sell never skips)
    eb, why = paper_exit_decision(0.10, None, "TP1", "enforce", 100,
                                  slip_pct=1.5, fee_usd=0.17)
    expected = 0.10 * (1 - 0.015 - 0.17/100)
    assert why == "fresh" and abs(eb - expected) < 1e-9
    eb0, _ = paper_exit_decision(0.10, 0.0, "TP1", "enforce", 100,
                                 slip_pct=1.5, fee_usd=0.17)
    assert abs(eb0 - expected) < 1e-9

def test_paper_exit_defaults_slip_fee_when_none():
    eb, why = paper_exit_decision(0.10, 0.095, "TP1", "shadow", 100)
    expected = 0.095 * (1 - measured_live_slip_pct()/100 - paper_fee_usd()/100)
    assert why == "fresh" and abs(eb - expected) < 1e-9

def test_paper_exit_fail_open_on_garbage():
    # garbage mid + stale fresh -> effective_fill returns the str unchanged, the
    # gap multiply raises -> outer fail-open returns decision_mid, never raises
    eb, why = paper_exit_decision("notaprice", None, "HARD_STOP", "enforce", 100)
    assert eb == "notaprice" and why == "error_fallback"


# --- caps_would_block (Task 8): mirror the LIVE per-token cap arithmetic ---

def test_caps_block_at_max_n():
    # open_n already at the cap -> block regardless of usd headroom
    assert caps_would_block(open_n=2, open_usd=0.0, size_usd=10.0,
                            max_n=2, max_usd=60.0) is True

def test_caps_block_over_max_usd():
    # under position cap but (open_usd + size_usd) exceeds the $ cap -> block
    assert caps_would_block(open_n=1, open_usd=55.0, size_usd=10.0,
                            max_n=2, max_usd=60.0) is True

def test_caps_pass_under_both():
    # below position cap AND within $ cap -> do not block
    assert caps_would_block(open_n=1, open_usd=40.0, size_usd=10.0,
                            max_n=2, max_usd=60.0) is False

def test_caps_exactly_at_usd_cap_passes():
    # boundary: (open_usd + size_usd) == max_usd is NOT > max_usd -> pass (mirror live)
    assert caps_would_block(open_n=0, open_usd=50.0, size_usd=10.0,
                            max_n=2, max_usd=60.0) is False

def test_caps_fail_open_on_none():
    # any None/garbage -> fail-open (do NOT block)
    assert caps_would_block(None, 0.0, 10.0, 2, 60.0) is False
    assert caps_would_block(1, None, 10.0, 2, 60.0) is False
    assert caps_would_block(1, 0.0, None, 2, 60.0) is False
    assert caps_would_block(1, 0.0, 10.0, None, 60.0) is False
    assert caps_would_block(1, 0.0, 10.0, 2, None) is False
    assert caps_would_block("x", "y", "z", "a", "b") is False
