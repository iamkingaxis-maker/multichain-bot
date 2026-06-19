"""Tests for the fill-speed P&L comparison tool (scripts/fill_speed_pnl.py).

The pure/testable core:
  - tier_entry_price(trajectory, anchor_ts, lead_secs)
  - tier_pnl_pct(entry_price, exit_price)

These reconstruct the counterfactual entry price the bot WOULD have gotten at
an earlier fill latency, and the realized P&L for the same exit.
"""
import importlib.util
import os

import pytest

# Load scripts/fill_speed_pnl.py by path (scripts/ is not a package).
_HERE = os.path.dirname(os.path.abspath(__file__))
_MOD_PATH = os.path.join(_HERE, "..", "scripts", "fill_speed_pnl.py")
_spec = importlib.util.spec_from_file_location("fill_speed_pnl", _MOD_PATH)
fsp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fsp)


# ── tier_entry_price ──────────────────────────────────────────────────────────

def test_tier_entry_price_exact_match():
    # trajectory: (ts, price). anchor=1000, lead=10 -> want price at ts<=990
    traj = [(980, 1.0), (990, 2.0), (1000, 3.0)]
    assert fsp.tier_entry_price(traj, 1000, 10) == 2.0


def test_tier_entry_price_nearest_before():
    # No exact tick at 990; nearest <= 990 is the 985 tick.
    traj = [(980, 1.0), (985, 1.5), (995, 2.5), (1000, 3.0)]
    # anchor=1000 lead=10 -> target=990 -> nearest <= 990 = 985 -> 1.5
    assert fsp.tier_entry_price(traj, 1000, 10) == 1.5


def test_tier_entry_price_zero_lead_uses_anchor():
    traj = [(980, 1.0), (1000, 3.0)]
    # lead 0 -> target=1000 -> price at 1000 = 3.0
    assert fsp.tier_entry_price(traj, 1000, 0) == 3.0


def test_tier_entry_price_none_when_no_data_before_target():
    traj = [(995, 2.5), (1000, 3.0)]
    # target=990; earliest tick is 995 > 990 -> no usable price -> None
    assert fsp.tier_entry_price(traj, 1000, 10) is None


def test_tier_entry_price_empty_trajectory():
    assert fsp.tier_entry_price([], 1000, 10) is None


def test_tier_entry_price_unsorted_input_handled():
    traj = [(1000, 3.0), (980, 1.0), (990, 2.0)]
    assert fsp.tier_entry_price(traj, 1000, 10) == 2.0


# ── tier_pnl_pct ──────────────────────────────────────────────────────────────

def test_tier_pnl_pct_gain():
    assert fsp.tier_pnl_pct(100.0, 110.0) == pytest.approx(10.0)


def test_tier_pnl_pct_loss():
    assert fsp.tier_pnl_pct(100.0, 90.0) == pytest.approx(-10.0)


def test_tier_pnl_pct_flat():
    assert fsp.tier_pnl_pct(100.0, 100.0) == 0.0


def test_tier_pnl_pct_zero_entry_is_none():
    assert fsp.tier_pnl_pct(0.0, 100.0) is None


def test_tier_pnl_pct_negative_entry_is_none():
    assert fsp.tier_pnl_pct(-1.0, 100.0) is None


def test_tier_pnl_pct_none_entry_is_none():
    assert fsp.tier_pnl_pct(None, 100.0) is None


def test_tier_pnl_pct_none_exit_is_none():
    assert fsp.tier_pnl_pct(100.0, None) is None


# ── summarize_tier (aggregation helper) ───────────────────────────────────────

def test_summarize_tier_basic():
    pnls = [10.0, -5.0, 20.0, 0.0]
    s = fsp.summarize_tier(pnls)
    assert s["n"] == 4
    # WR = pnl > 0 strictly: 10 and 20 -> 2/4 = 50%
    assert s["wr"] == 50.0
    assert s["sum"] == 25.0
    assert s["mean"] == 25.0 / 4
    assert s["median"] == (0.0 + 10.0) / 2  # sorted [-5,0,10,20] -> median 5.0


def test_summarize_tier_empty():
    s = fsp.summarize_tier([])
    assert s["n"] == 0
    assert s["wr"] is None
    assert s["median"] is None
    assert s["mean"] is None
    assert s["sum"] == 0.0


def test_summarize_tier_drops_none():
    s = fsp.summarize_tier([10.0, None, -5.0, None])
    assert s["n"] == 2
    assert s["sum"] == 5.0


# ── verdict ───────────────────────────────────────────────────────────────────

def test_verdict_helps():
    # fast median > slow median by a clear margin
    v = fsp.verdict_line(fast_median=15.0, slow_median=5.0, n=10,
                          fast_label="3s", slow_label="85s")
    assert "HELPS" in v


def test_verdict_hurts():
    v = fsp.verdict_line(fast_median=2.0, slow_median=12.0, n=10,
                         fast_label="3s", slow_label="85s")
    assert "HURTS" in v


def test_verdict_neutral_small_delta():
    v = fsp.verdict_line(fast_median=5.2, slow_median=5.0, n=10,
                         fast_label="3s", slow_label="85s")
    assert "NEUTRAL" in v


def test_verdict_none_when_no_data():
    v = fsp.verdict_line(fast_median=None, slow_median=5.0, n=0,
                         fast_label="3s", slow_label="85s")
    assert "NO DATA" in v or "no data" in v.lower()
