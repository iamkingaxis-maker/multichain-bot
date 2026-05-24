"""Unit tests for check_exhaustion_realtime — pre-TP1 soft trail.

Verifies:
  - SOFT TRAIL arms when peak >= 2.5 AND drop >= 1.5pp AND pnl <= -2%
  - Arming doesn't fire — 60s confirm window must elapse
  - Recovery within window disarms pending
  - Pre-peak (peak < 2.5%) drops don't arm anything
  - dip_buy strategy gate honored (other strategies are no-ops)
  - tp1_hit / stop_triggered / trail_triggered guards prevent double-fires

History: pre-TP1 HARD GUARD was removed 2026-05-18 (universe-recorder sim
showed it cost -1.49pp/trade, -$801/day at $20 size — fundamentally
incompatible with runner-tilt thesis). The hard-guard test that lived
here was removed alongside; the soft-trail tests below cover the
surviving exit paths.
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


def test_first_tick_below_pnl_floor_arms_but_does_not_fire():
    """Replaces removed test_hard_guard_fires. The hard guard was deleted
    2026-05-18; the soft trail arms instead and waits for the 60s confirm
    window. Same scenario (peak +3, then drop to -2.5) should result in
    arming, not firing on the first tick."""
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    pm.check_exhaustion_realtime("ADDR1", 1.03)
    assert _fire_calls(pm) == 0
    pm.check_exhaustion_realtime("ADDR1", 0.975)
    # Soft trail arms (drop=5.5pp >= 1.5, pnl=-2.5 <= -2 floor) but
    # confirmation window has not elapsed yet, so no fire.
    assert state.pending_exit_since_ts is not None
    assert _fire_calls(pm) == 0


def test_does_not_arm_when_peak_below_min_threshold():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Peak only +2% (below MIN_PEAK of 2.5)
    pm.check_exhaustion_realtime("ADDR1", 1.02)
    # Drop to -3% — peak never crossed MIN_PEAK, so soft trail inactive
    pm.check_exhaustion_realtime("ADDR1", 0.97)
    assert _fire_calls(pm) == 0
    assert state.pending_exit_since_ts is None


def test_soft_trail_arms_on_first_breach():
    """Post-2026-05-19 carve-out: soft trail only arms when pnl drops below
    -2% (PNL_FLOOR). Pure drop-from-peak with positive pnl no longer arms —
    that scenario is the runner-tilt thesis preserving room to move."""
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Walk peak to +3%
    pm.check_exhaustion_realtime("ADDR1", 1.03)
    assert state.pending_exit_since_ts is None
    # Drop to -2.5% — pnl below floor, drop=5.5pp (not panic at 6pp)
    pm.check_exhaustion_realtime("ADDR1", 0.975)
    assert state.pending_exit_since_ts is not None
    assert _fire_calls(pm) == 0  # not yet fired (confirm window not elapsed)


def test_soft_trail_does_not_arm_below_min_peak():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Walk peak to +2.0% (below MIN_PEAK of 2.5)
    pm.check_exhaustion_realtime("ADDR1", 1.02)
    # Drop to -3% (below floor) — but peak never crossed MIN_PEAK, no arm
    pm.check_exhaustion_realtime("ADDR1", 0.97)
    assert state.pending_exit_since_ts is None


def test_soft_trail_fires_after_confirm_window():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Peak +3%
    pm.check_exhaustion_realtime("ADDR1", 1.03)
    # Drop to -2.5% — arms
    pm.check_exhaustion_realtime("ADDR1", 0.975)
    assert state.pending_exit_since_ts is not None
    # Simulate 60+ seconds passing
    state.pending_exit_since_ts = time_module.monotonic() - 65.0
    # Another tick still below threshold
    pm.check_exhaustion_realtime("ADDR1", 0.975)
    assert _fire_calls(pm) == 1


def test_soft_trail_disarms_on_recovery():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Peak +3%
    pm.check_exhaustion_realtime("ADDR1", 1.03)
    # Drop to -2.5% — arms (drop=5.5pp, pnl=-2.5 <= floor)
    pm.check_exhaustion_realtime("ADDR1", 0.975)
    assert state.pending_exit_since_ts is not None
    # Recover to +2.5% — drop = 0.5pp, below _RECOVERY_PP (1.0) → disarm
    pm.check_exhaustion_realtime("ADDR1", 1.025)
    assert state.pending_exit_since_ts is None
    assert _fire_calls(pm) == 0


def test_soft_trail_does_not_disarm_on_partial_recovery():
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Peak +3%, drop to -2.5% to arm
    pm.check_exhaustion_realtime("ADDR1", 1.03)
    pm.check_exhaustion_realtime("ADDR1", 0.975)
    assert state.pending_exit_since_ts is not None
    # Recover only to +1.5% — drop = 1.5pp, still above _RECOVERY_PP (1.0).
    # Pnl is now above floor so wouldn't re-arm, but recovery isn't deep
    # enough to disarm either. Pending state persists.
    pm.check_exhaustion_realtime("ADDR1", 1.015)
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


def test_fish_class_scenario_arms_then_disarms_on_recovery():
    """Updated for 2026-05-19 carve-out. Original fish-class case (peak
    +3.5%, dip to +1.5%, recover) no longer arms at all under the new
    PNL_FLOOR — pnl never goes underwater. Recast to a deeper-dip scenario
    that actually arms then recovers, which is the spirit of the test
    (verify recovery cancels pending state)."""
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Climb to +3.5% peak
    pm.check_exhaustion_realtime("ADDR1", 1.035)
    # Dip to -2.5% — arms (drop=6.0pp is exactly panic threshold, so 5.99
    # to stay non-panic; using -2.4 keeps drop=5.9pp and pnl<floor)
    pm.check_exhaustion_realtime("ADDR1", 0.976)
    assert state.pending_exit_since_ts is not None
    # Quickly recover to +2.7% — drop = 0.8pp <= _RECOVERY_PP → disarm
    pm.check_exhaustion_realtime("ADDR1", 1.027)
    assert state.pending_exit_since_ts is None
    # Continue up to +5% — should not fire
    pm.check_exhaustion_realtime("ADDR1", 1.05)
    assert _fire_calls(pm) == 0


def test_panic_exit_fires_on_catastrophic_drop():
    """Replaces test_director_class_scenario_hard_guard_catches_fast_reversal.
    The hard guard was removed 2026-05-18; the surviving fast-reversal path
    is the panic exit (drop >= 6pp, 5s confirm) added 2026-05-19 for
    memecoins-class catastrophic give-backs."""
    pm = _build_pm()
    state = _make_state(entry_price=1.0)
    pm._states["addr1"] = state
    # Peak +3.1% — just above MIN_PEAK
    pm.check_exhaustion_realtime("ADDR1", 1.031)
    # Catastrophic drop to -3.5% — drop=6.6pp (>= PANIC_DROP_PP=6.0), arms
    pm.check_exhaustion_realtime("ADDR1", 0.965)
    assert state.pending_exit_since_ts is not None
    # Backdate the arm by 6s — exceeds PANIC_CONFIRM_S=5
    state.pending_exit_since_ts = time_module.monotonic() - 6.0
    # Next tick still panic-deep — fires
    pm.check_exhaustion_realtime("ADDR1", 0.965)
    assert _fire_calls(pm) == 1
    args = _last_fire_args(pm)
    assert "panic" in args[2].lower()


if __name__ == "__main__":
    test_first_tick_below_pnl_floor_arms_but_does_not_fire()
    test_does_not_arm_when_peak_below_min_threshold()
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
