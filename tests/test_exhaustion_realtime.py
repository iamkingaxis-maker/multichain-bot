"""Unit tests for check_exhaustion_realtime — the Option A hybrid trail.

Verifies:
  - HARD GUARD fires immediately at -2% absolute pnl when peak >= +3%
  - SOFT TRAIL arms when drop >= 1.5pp, doesn't fire until 60s elapses
  - Recovery within window disarms pending
  - Pre-peak (peak < +3%) drops don't arm anything
  - dip_buy strategy gate honored (other strategies are no-ops)
  - tp1_hit / stop_triggered / trail_triggered guards prevent double-fires
"""
import os
import sys
import time as time_module
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio


def _build_pm():
    """Minimal PositionManager built for unit testing — only what
    check_exhaustion_realtime touches."""
    from core.position_manager import PositionManager, PositionState
    import core.position_manager as pm_mod

    pm = PositionManager.__new__(PositionManager)
    pm._states = {}
    pm._trail_triggered = set()
    pm._stop_triggered = set()
    pm._tp_triggered = set()
    pm._last_realtime_price = {}
    pm.open_positions_ref = {}
    pm.chain_name = "solana"

    # Capture _do_pre_tp1_realtime_sell calls without actually scheduling them
    # on an event loop. Replace both the method (so it returns a no-op
    # coroutine ensure_future can accept) and asyncio.ensure_future itself
    # so we record what would be scheduled.
    pm._do_pre_tp1_realtime_sell = MagicMock()
    pm._do_pre_tp1_realtime_sell.call_args_list_real = []

    async def _noop_coro():
        return None

    def _capture_call(*args, **kwargs):
        # First call records args; return a real coroutine for ensure_future
        pm._do_pre_tp1_realtime_sell.call_args_list_real.append((args, kwargs))
        return _noop_coro()

    pm._do_pre_tp1_realtime_sell.side_effect = _capture_call

    # Patch asyncio.ensure_future in the position_manager module's namespace
    # to be a no-op so we don't need a running loop. The mock's side_effect
    # already captured the real call args before this is reached.
    pm._captured_ef = []

    def _fake_ef(coro):
        pm._captured_ef.append(coro)
        # Consume the coroutine so we don't leak warnings
        try:
            coro.close()
        except Exception:
            pass
        return None

    pm_mod.asyncio.ensure_future = _fake_ef
    return pm


def _fire_calls(pm):
    """How many times _do_pre_tp1_realtime_sell was invoked with real args."""
    return len(pm._do_pre_tp1_realtime_sell.call_args_list_real)


def _last_fire_args(pm):
    return pm._do_pre_tp1_realtime_sell.call_args_list_real[-1][0]


def _make_state(entry_price=1.0, tp1_hit=False, strategy="dip_buy", age_s=10):
    from core.position_manager import PositionState
    return PositionState(
        token_address="ADDR1",
        token_symbol="TOK",
        chain_id="solana",
        entry_price=entry_price,
        entry_volume_usd=1000.0,
        position_size_usd=20.0,
        original_size_usd=20.0,
        entry_time=datetime.now(timezone.utc) - timedelta(seconds=age_s),
        strategy=strategy,
        tp1_hit=tp1_hit,
        peak_price=entry_price,  # default starts at entry
    )


def test_hard_guard_fires_when_pnl_below_minus_2_with_peak_above_3():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Walk up to peak +3% first
    pm.check_exhaustion_realtime("ADDR1", 1.03)
    assert _fire_calls(pm) == 0
    # Drop to -2.5% (below hard guard)
    pm.check_exhaustion_realtime("ADDR1", 0.975)
    assert _fire_calls(pm) == 1
    args = _last_fire_args(pm)
    assert args[0] == "addr1"
    assert "hard guard" in args[2].lower()


def test_hard_guard_does_not_fire_below_minus_2_without_peak_above_3():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Peak only +2% (below MIN_PEAK)
    pm.check_exhaustion_realtime("ADDR1", 1.02)
    # Drop to -3% — but peak never crossed +3%, so hard guard inactive
    pm.check_exhaustion_realtime("ADDR1", 0.97)
    assert _fire_calls(pm) == 0


def test_soft_trail_arms_on_first_breach():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Walk peak to +5%
    pm.check_exhaustion_realtime("ADDR1", 1.05)
    assert state.pending_exit_since_ts is None
    # Drop to +3% (drop = 2.0pp >= 1.5pp threshold)
    pm.check_exhaustion_realtime("ADDR1", 1.03)
    assert state.pending_exit_since_ts is not None
    assert _fire_calls(pm) == 0  # not yet fired


def test_soft_trail_does_not_arm_below_min_peak():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Walk peak to +2.5% (below MIN_PEAK of 3.0)
    pm.check_exhaustion_realtime("ADDR1", 1.025)
    # Drop to +0.5%
    pm.check_exhaustion_realtime("ADDR1", 1.005)
    assert state.pending_exit_since_ts is None  # never armed


def test_soft_trail_fires_after_confirm_window():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Peak +5%
    pm.check_exhaustion_realtime("ADDR1", 1.05)
    # Drop to +3% — arms
    pm.check_exhaustion_realtime("ADDR1", 1.03)
    assert state.pending_exit_since_ts is not None
    # Simulate 60+ seconds passing
    state.pending_exit_since_ts = time_module.monotonic() - 65.0
    # Another tick still below threshold
    pm.check_exhaustion_realtime("ADDR1", 1.03)
    assert _fire_calls(pm) == 1


def test_soft_trail_disarms_on_recovery():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Peak +5%
    pm.check_exhaustion_realtime("ADDR1", 1.05)
    # Drop to +3% — arms
    pm.check_exhaustion_realtime("ADDR1", 1.03)
    assert state.pending_exit_since_ts is not None
    # Recover to +4.5% — drop is only 0.5pp from peak, below 1.0 recovery threshold
    pm.check_exhaustion_realtime("ADDR1", 1.045)
    assert state.pending_exit_since_ts is None
    assert _fire_calls(pm) == 0


def test_soft_trail_does_not_disarm_on_partial_recovery():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    pm.check_exhaustion_realtime("ADDR1", 1.05)
    pm.check_exhaustion_realtime("ADDR1", 1.03)
    # Recover only to +3.5% — drop is 1.5pp, still at threshold (not above recovery)
    pm.check_exhaustion_realtime("ADDR1", 1.035)
    assert state.pending_exit_since_ts is not None  # still armed


def test_no_action_when_tp1_already_hit():
    pm = _build_pm()
    state = _make_state(entry_price=1.0, tp1_hit=True)
    pm._states["addr1"] = state
    pm.check_exhaustion_realtime("ADDR1", 1.05)
    pm.check_exhaustion_realtime("ADDR1", 0.97)  # below -2%
    assert _fire_calls(pm) == 0


def test_no_action_for_non_dip_strategy():
    pm = _build_pm()
    state = _make_state(entry_price=1.0, strategy="scanner")
    pm._states["addr1"] = state
    pm.check_exhaustion_realtime("ADDR1", 1.05)
    pm.check_exhaustion_realtime("ADDR1", 0.97)
    assert _fire_calls(pm) == 0


def test_no_action_in_first_5_seconds():
    pm = _build_pm()
    state = _make_state(entry_price=1.0, age_s=2)
    pm._states["addr1"] = state
    pm.check_exhaustion_realtime("ADDR1", 1.05)
    pm.check_exhaustion_realtime("ADDR1", 0.97)
    assert _fire_calls(pm) == 0


def test_already_triggered_prevents_double_fire():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    pm._trail_triggered.add("addr1")
    pm.check_exhaustion_realtime("ADDR1", 1.05)
    pm.check_exhaustion_realtime("ADDR1", 0.97)
    assert _fire_calls(pm) == 0


def test_stop_in_flight_prevents_trail():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    pm._stop_triggered.add("addr1")
    pm.check_exhaustion_realtime("ADDR1", 1.05)
    pm.check_exhaustion_realtime("ADDR1", 0.97)
    assert _fire_calls(pm) == 0


def test_spike_sanity_gate_rejects_giant_tick():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    pm._last_realtime_price["addr1"] = 1.0
    # Tick at 1.5x (50% jump) — should reject as glitch
    pm.check_exhaustion_realtime("ADDR1", 1.5)
    # peak shouldn't have updated
    assert state.peak_price == 1.0
    # tick at 0.5x (50% drop) — also rejected
    pm.check_exhaustion_realtime("ADDR1", 0.5)
    assert _fire_calls(pm) == 0


def test_fish_class_scenario_holds_through_oscillation():
    """Mirror of FAHHHH/fish case: peak +3.5%, dip briefly to +1%, recover to
    +4% and continue up. Should NOT fire — recovery cancels pending."""
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Climb to +3.5% peak
    pm.check_exhaustion_realtime("ADDR1", 1.035)
    # Dip to +1.5% — arms (drop = 2.0pp)
    pm.check_exhaustion_realtime("ADDR1", 1.015)
    assert state.pending_exit_since_ts is not None
    # Quickly recover to +3.0% — drop = 0.5pp <= 1.0pp recovery margin → disarm
    pm.check_exhaustion_realtime("ADDR1", 1.03)
    assert state.pending_exit_since_ts is None
    # Continue up to +5%
    pm.check_exhaustion_realtime("ADDR1", 1.05)
    assert _fire_calls(pm) == 0


def test_director_class_scenario_hard_guard_catches_fast_reversal():
    """Mirror of DIRECTOR case: peak +3.2%, then fast drop straight to -3%.
    Soft trail would arm and need 60s; hard guard catches it earlier."""
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Climb to +3.2% peak
    pm.check_exhaustion_realtime("ADDR1", 1.032)
    # Soft trail arms at +1.7% (drop 1.5pp)
    pm.check_exhaustion_realtime("ADDR1", 1.017)
    assert state.pending_exit_since_ts is not None
    # Fast drop to -2.5% (hard guard threshold)
    pm.check_exhaustion_realtime("ADDR1", 0.975)
    assert _fire_calls(pm) == 1
    args = _last_fire_args(pm)
    assert "hard guard" in args[2].lower()


if __name__ == "__main__":
    test_hard_guard_fires_when_pnl_below_minus_2_with_peak_above_3()
    test_hard_guard_does_not_fire_below_minus_2_without_peak_above_3()
    test_soft_trail_arms_on_first_breach()
    test_soft_trail_does_not_arm_below_min_peak()
    test_soft_trail_fires_after_confirm_window()
    test_soft_trail_disarms_on_recovery()
    test_soft_trail_does_not_disarm_on_partial_recovery()
    test_no_action_when_tp1_already_hit()
    test_no_action_for_non_dip_strategy()
    test_no_action_in_first_5_seconds()
    test_already_triggered_prevents_double_fire()
    test_stop_in_flight_prevents_trail()
    test_spike_sanity_gate_rejects_giant_tick()
    test_fish_class_scenario_holds_through_oscillation()
    test_director_class_scenario_hard_guard_catches_fast_reversal()
    print("All exhaustion realtime tests passed")
