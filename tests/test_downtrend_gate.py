"""Tests for the watchlist-bypass downtrend gate (SHADOW).

Reference: TROLL 2026-05-26 entry meta (pc_h1=-14.05, pc_h6=-41.78,
pc_m5=-3.14) — bought 108× into a still-falling slide. neet (+$116) was the
same size class but trending favorably.
"""

import core.downtrend_gate as dg


def test_troll_falling_knife_blocks():
    meta = {"pc_h1": -14.05, "pc_h6": -41.78, "pc_m5": -3.14}
    v, reasons = dg.downtrend_verdict(meta)
    assert v == "BLOCK"
    assert any("pc_m5" in r for r in reasons)


def test_deep_dip_but_reversing_passes():
    # deeply red on h6/h1 but bouncing now (pc_m5 > 0) → a real dip, keep it
    meta = {"pc_h1": -14.0, "pc_h6": -40.0, "pc_m5": +2.5}
    v, _ = dg.downtrend_verdict(meta)
    assert v == "PASS"


def test_shallow_dip_passes():
    # normal shallow pullback — nowhere near sustained-decline thresholds
    meta = {"pc_h1": -3.0, "pc_h6": -6.0, "pc_m5": -1.0}
    v, _ = dg.downtrend_verdict(meta)
    assert v == "PASS"


def test_deep_h6_but_shallow_h1_passes():
    # h6 deep but h1 recovered above threshold → not a current knife
    meta = {"pc_h1": -2.0, "pc_h6": -30.0, "pc_m5": -1.0}
    v, _ = dg.downtrend_verdict(meta)
    assert v == "PASS"


def test_still_falling_via_lookback_blocks():
    # pc_m5 flat/None but pc_h1_change_since_lookback negative → still falling
    meta = {"pc_h1": -12.0, "pc_h6": -25.0, "pc_h1_change_since_lookback": -7.15}
    v, reasons = dg.downtrend_verdict(meta)
    assert v == "BLOCK"
    assert any("lookback" in r for r in reasons)


def test_still_falling_via_structure_blocks():
    meta = {"pc_h1": -10.0, "pc_h6": -20.0, "chart_structure_5m_state": "downtrend"}
    v, reasons = dg.downtrend_verdict(meta)
    assert v == "BLOCK"
    assert any("structure" in r for r in reasons)


def test_sustained_decline_no_falling_signal_passes():
    # deep on both TFs but no pc_m5 / lookback / structure evidence of falling →
    # treat as reversing (fail toward keeping the entry)
    meta = {"pc_h1": -12.0, "pc_h6": -25.0}
    v, _ = dg.downtrend_verdict(meta)
    assert v == "PASS"


def test_missing_core_fields_unknown():
    assert dg.downtrend_verdict({})[0] == "UNKNOWN"
    assert dg.downtrend_verdict({"pc_h1": -10.0})[0] == "UNKNOWN"  # missing pc_h6


def test_thresholds_tunable():
    # a -10/-12 token passes default (-15 h6) but blocks under a looser h6 cap
    meta = {"pc_h1": -10.0, "pc_h6": -12.0, "pc_m5": -1.0}
    assert dg.downtrend_verdict(meta)[0] == "PASS"
    assert dg.downtrend_verdict(meta, pc_h6_max=-10.0)[0] == "BLOCK"
