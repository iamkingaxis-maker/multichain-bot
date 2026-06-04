"""forward_label math for the wallet-HHI grad mine (scripts/wallet_hhi_grad_mine.py).
The resolution label runs unattended in the accrual loop, so it gets a unit test."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "wallet_hhi_grad_mine",
    Path(__file__).parent.parent / "scripts" / "wallet_hhi_grad_mine.py",
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)
forward_label = mod.forward_label


def _b(ts_ms, high, close):
    return {"ts_ms": ts_ms, "high": high, "close": close}


def test_pump_when_peak_exceeds_threshold():
    bars = [_b(1000, 100, 100), _b(2000, 140, 130), _b(3000, 120, 110)]
    lab = forward_label(100.0, 500, bars, horizon_min=60, pump_x=1.30)
    assert lab["fwd_peak_x"] == 1.4   # 140/100
    assert lab["pump"] == 1


def test_bleed_when_peak_below_threshold():
    bars = [_b(2000, 110, 90), _b(3000, 105, 70)]
    lab = forward_label(100.0, 500, bars, horizon_min=60, pump_x=1.30)
    assert lab["fwd_peak_x"] == 1.1
    assert lab["fwd_end_x"] == 0.7
    assert lab["pump"] == 0


def test_no_forward_bars_is_bleed_to_zero():
    # all bars are at/before the anchor ts -> token died, no forward data
    bars = [_b(400, 100, 100), _b(500, 100, 100)]
    lab = forward_label(100.0, 500, bars, horizon_min=60, pump_x=1.30)
    assert lab == {"fwd_peak_x": 0.0, "fwd_end_x": 0.0, "pump": 0}


def test_horizon_window_excludes_later_bars():
    # bar at 5_000_000ms past anchor is beyond a 60min window -> ignored
    bars = [_b(600, 120, 115), _b(500 + 61 * 60_000, 999, 999)]
    lab = forward_label(100.0, 500, bars, horizon_min=60, pump_x=1.30)
    assert lab["fwd_peak_x"] == 1.2   # the 999 bar is outside the horizon
    assert lab["pump"] == 0


def test_bad_anchor_returns_none():
    assert forward_label(0, 500, [_b(600, 100, 100)]) is None
    assert forward_label(None, 500, [_b(600, 100, 100)]) is None
