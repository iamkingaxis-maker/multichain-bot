"""Units for the per-trigger token-state SHADOW (2026-06-10)."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core.trigger_state_gates import trigger_state_verdicts, TRIGGER_STATE_GATES


def test_pass_block_na_verdicts():
    feats = {"pct_off_peak": -30.0, "time_since_h1_peak_secs": 5000.0}
    v = trigger_state_verdicts(
        ("deep_1h_dip", "pullback_in_uptrend", "whale_conviction", "unmapped_trig"),
        feats)
    assert v["deep_1h_dip"] == "pass"            # -30 <= -24
    assert v["pullback_in_uptrend"] == "block"   # 5000 > 2820 (stale peak)
    assert v["whale_conviction"] == "na"         # feature missing
    assert "unmapped_trig" not in v              # no gate -> no verdict


def test_hot_vs_calm_flow_opposite_splits():
    # the decisive-proof pair: same feature, opposite directions
    hot = {"buy_pressure_60s": 0.70}
    calm = {"buy_pressure_60s": 0.30}
    v_hot = trigger_state_verdicts(("informed_cluster", "swing_structure_rsi"), hot)
    v_calm = trigger_state_verdicts(("informed_cluster", "swing_structure_rsi"), calm)
    assert v_hot == {"informed_cluster": "block", "swing_structure_rsi": "pass"}
    assert v_calm == {"informed_cluster": "pass", "swing_structure_rsi": "block"}


def test_fail_soft_on_garbage():
    assert trigger_state_verdicts(None, None) == {}
    assert trigger_state_verdicts(("deep_1h_dip",), {"pct_off_peak": "junk"}) == {
        "deep_1h_dip": "na"}


def test_gate_map_shape():
    for trig, (feat, op, thr) in TRIGGER_STATE_GATES.items():
        assert op in ("<=", ">="), trig
        assert isinstance(thr, float), trig
