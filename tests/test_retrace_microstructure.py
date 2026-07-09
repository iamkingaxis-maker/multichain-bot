# tests/test_retrace_microstructure.py
"""Retrace micro-structure gate (2026-07-09 on-chain fleet). Step B (sell
distribution AVOID) is the shippable hard-block; Step C (net-flow persistence)
is the shadow corroborator. Both forward-only, fail-open on thin data."""
from core.retrace_microstructure import (
    sell_distribution_flag, net_flow_persistence, retrace_micro_eval, _epoch,
)

REF = 1_000_000.0  # epoch ref = "now"


def _t(kind, usd, dt):  # dt = seconds relative to ref (negative = before)
    return {"kind": kind, "volume_usd": usd, "ts": REF + dt}


class TestSellDistribution:
    def test_heavy_accelerating_sells_block(self):
        # heavy ($/s well over 18) AND accelerating (late 30s >> early 30s)
        tr = [_t("sell", 200, -55), _t("sell", 300, -45),          # early: 500
              _t("sell", 600, -20), _t("sell", 800, -8), _t("buy", 50, -5)]  # late: 1400
        r = sell_distribution_flag(tr, REF)
        assert r["block"] is True
        assert r["sell_rate_60"] >= 18 and r["sell_traj"] >= 1.0

    def test_drying_sells_pass(self):
        # sells DEcelerating (early heavy, late light) -> not distribution
        tr = [_t("sell", 800, -55), _t("sell", 700, -45),          # early: 1500
              _t("sell", 50, -20), _t("buy", 400, -10), _t("buy", 300, -5)]  # late sells: 50
        r = sell_distribution_flag(tr, REF)
        assert r["block"] is False and r["sell_traj"] < 1.0

    def test_light_sells_pass_even_if_accelerating(self):
        # accelerating but tiny $/s (< 18) -> not heavy -> pass
        tr = [_t("sell", 5, -50), _t("sell", 20, -10), _t("buy", 500, -5)]
        r = sell_distribution_flag(tr, REF)
        assert r["block"] is False   # sell_rate_60 ~0.4 < 18

    def test_too_few_trades_fail_open(self):
        assert sell_distribution_flag([_t("sell", 999, -5)], REF)["block"] is False
        assert sell_distribution_flag([], REF)["block"] is False

    def test_bad_ref_fail_open(self):
        assert sell_distribution_flag([_t("sell", 999, -5)], None)["block"] is False


class TestNetFlowPersistence:
    def test_sustained_inflow_confirms(self):
        # >$300 cum AND net-positive in >=2 of 3 sub-windows (each 20s)
        tr = [_t("buy", 200, -55), _t("sell", 50, -50),   # subwin0 [-60,-40]: +150
              _t("buy", 200, -35), _t("sell", 20, -30),   # subwin1 [-40,-20]: +180
              _t("buy", 100, -10)]                        # subwin2 [-20,0]:  +100
        r = net_flow_persistence(tr, REF)
        assert r["confirm"] is True and r["pos_subwins"] >= 2 and r["cum_nf_60"] >= 300

    def test_single_tick_spike_rejected(self):
        # one big buy in one subwin, negative elsewhere -> not persistent (Bullchuriki)
        tr = [_t("sell", 100, -55), _t("sell", 80, -35), _t("buy", 900, -10)]
        r = net_flow_persistence(tr, REF)
        assert r["confirm"] is False   # only 1 positive subwin

    def test_insufficient_dollars_rejected(self):
        tr = [_t("buy", 50, -50), _t("buy", 40, -30), _t("buy", 30, -10)]
        r = net_flow_persistence(tr, REF)
        assert r["confirm"] is False   # cum < 300


class TestCombine:
    def test_eval_shape(self):
        tr = [_t("sell", 600, -20), _t("sell", 800, -8), _t("sell", 300, -45),
              _t("sell", 200, -55)]
        r = retrace_micro_eval(tr, REF)
        assert "avoid_block" in r and "flow_confirm" in r and "sell" in r


class TestEpoch:
    def test_iso_and_epoch(self):
        assert abs(_epoch(1000.0) - 1000.0) < 1e-6
        assert _epoch("2026-07-04T12:15:38+00:00") is not None
        assert _epoch(None) is None and _epoch("garbage") is None
