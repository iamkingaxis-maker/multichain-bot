# tests/test_min_hold_floor.py — MIN-HOLD "no-panic" FLOOR + trailing-heat runner lift
"""The min-hold floor suppresses every soft cutter AND the -12 hard stop for the first
min_hold_floor_secs of a PRE-TP1 position, keeping ONLY the -25 rug tripwire. TP1/TP2
gains still fire. It is a FLOOR: once it expires (or TP1 hits) the normal ladder resumes.
Off by default (secs=0) => byte-identical.

The heat-gated runner lift raises TP2 +12 -> tp2_pct_hot when the trailing-heat regime
was HIGH at entry; TP1 and the stop are untouched; cold/off => tp2_pct exactly.
"""
import os

import pytest

from core.bot_config import BotConfig
from core.bot_evaluator import (
    min_hold_floor_active,
    min_hold_rug_tripwire_fires,
)
from core.per_bot_position_manager import PerBotPositionManager
import core.heat_regime as heat_regime


# ---- pure helpers ----
class TestPureHelpers:
    def test_active_within_window_pre_tp1(self):
        assert min_hold_floor_active(hold_secs=60, tp1_hit=False, floor_secs=120) is True

    def test_inactive_after_window(self):
        assert min_hold_floor_active(120, False, 120) is False
        assert min_hold_floor_active(200, False, 120) is False

    def test_inactive_when_tp1_hit(self):
        assert min_hold_floor_active(30, True, 120) is False

    def test_off_when_floor_zero(self):
        assert min_hold_floor_active(1, False, 0) is False

    def test_failsafe_bad_data(self):
        assert min_hold_floor_active(None, False, 120) is False
        assert min_hold_floor_active(float("nan"), False, 120) is False

    def test_rug_tripwire_fires_below_threshold(self):
        f, why = min_hold_rug_tripwire_fires(-26, -25)
        assert f is True and "rug" in why

    def test_rug_tripwire_holds_above_threshold(self):
        assert min_hold_rug_tripwire_fires(-24.9, -25)[0] is False
        assert min_hold_rug_tripwire_fires(-11, -25)[0] is False

    def test_rug_failsafe(self):
        assert min_hold_rug_tripwire_fires(None, -25)[0] is False


# ---- position-manager integration ----
def _pm(**over):
    base = dict(bot_id="badday_t", display_name="t", tp1_pct=6.0, tp1_sell_fraction=0.75,
                tp2_pct=12.0, tp2_sell_fraction=0.25, trail_pp=2.0, hard_stop_pct=-12.0,
                fast_bail_pnl_pct=-9.0, giveback_floor_peak_min=4.0,
                giveback_floor_pnl_pct=-6.0, never_runner_exit_enabled=True,
                never_runner_peak_max=3.0, never_runner_loss_floor=-6.0,
                min_hold_floor_secs=120.0, min_hold_floor_rug_pct=-25.0)
    base.update(over)
    return PerBotPositionManager(BotConfig(**base))


def _open(pm, price=1.0, t=1000.0):
    pm.open_position(token="TOK", entry_price=price, size_usd=25.0, entry_time=t,
                     address="mintTOK", pair_address="pairTOK")
    return pm.get_position("TOK")


def _tick(pm, pnl_pct, now, vol=10000.0):
    px = 1.0 * (1 + pnl_pct / 100.0)
    return pm.tick("TOK", px, now, vol_m5_usd=vol)


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    monkeypatch.setenv("MIN_HOLD_FLOOR_MODE", "enforce")
    monkeypatch.setenv("IN_FLIGHT_FLOOR_MODE", "enforce")
    heat_regime.reset()
    yield
    heat_regime.reset()


class TestFloorSuppressesSoftCutters:
    def test_shallow_dip_held_inside_floor(self):
        # -8% at 30s: would normally trip fast_bail(-9? no) / giveback / in-flight floor;
        # inside the floor it must be HELD (no decision).
        pm = _pm()
        _open(pm)
        assert _tick(pm, -8.0, now=1030.0) == []

    def test_deep_dip_below_hardstop_held_inside_floor(self):
        # -13% at 60s: past the -12 hard stop but ABOVE the -25 rug tripwire -> HELD.
        pm = _pm()
        _open(pm)
        assert _tick(pm, -13.0, now=1060.0) == []

    def test_fast_bail_held_inside_floor(self):
        pm = _pm()
        _open(pm)
        assert _tick(pm, -9.5, now=1040.0) == []  # fast_bail -9 suppressed

    def test_rug_tripwire_fires_inside_floor(self):
        pm = _pm()
        _open(pm)
        d = _tick(pm, -26.0, now=1050.0)
        assert [x.kind for x in d] == ["HARD_STOP"]
        assert "rug tripwire" in d[0].reason

    def test_cutters_resume_after_floor_expires(self):
        # same -13% but at 130s (past the 120s floor) -> a cutter fires. For a badday_
        # bot the tighter -7 IN_FLIGHT_FLOOR fires first (before the -12 hard stop).
        pm = _pm()
        _open(pm)
        d = _tick(pm, -13.0, now=1130.0)
        assert [x.kind for x in d] == ["IN_FLIGHT_FLOOR"]

    def test_tp1_still_fires_inside_floor(self):
        # a rip to +6 at 20s must still take TP1 (winner-safe).
        pm = _pm()
        _open(pm)
        d = _tick(pm, 6.5, now=1020.0)
        assert [x.kind for x in d] == ["TP1"]

    def test_stamp_records_counterfactual(self):
        pm = _pm()
        p = _open(pm)
        _tick(pm, -8.0, now=1030.0)
        assert p.state_blob.get("mhf_active") is True
        assert p.state_blob.get("mhf_first_secs") == 30


class TestShadowModeDoesNotAct:
    def test_shadow_mode_lets_cutter_fire(self, monkeypatch):
        monkeypatch.setenv("MIN_HOLD_FLOOR_MODE", "shadow")
        pm = _pm()
        p = _open(pm)
        d = _tick(pm, -13.0, now=1060.0)  # cutters NOT suppressed in shadow
        assert [x.kind for x in d] == ["IN_FLIGHT_FLOOR"]
        assert p.state_blob.get("mhf_active") is True  # but the counterfactual is stamped


class TestFloorOffByteIdentical:
    def test_off_when_secs_zero(self):
        pm = _pm(min_hold_floor_secs=0.0)
        _open(pm)
        d = _tick(pm, -13.0, now=1030.0)
        assert [x.kind for x in d] == ["IN_FLIGHT_FLOOR"]  # normal ladder, unsuppressed

    def test_default_config_floor_off(self):
        cfg = BotConfig(bot_id="x", display_name="x")
        assert cfg.min_hold_floor_secs == 0.0


class TestHeatRunnerLift:
    def test_cold_regime_keeps_tp2_12(self):
        heat_regime.reset()  # empty window -> COLD
        pm = _pm(regime_runner_lift=True, tp2_pct_hot=18.0, min_hold_floor_secs=0.0)
        p = _open(pm)
        _tick(pm, 6.5, now=1020.0)                 # TP1
        assert p.state_blob.get("heat_high_at_entry") is False
        d = _tick(pm, 12.5, now=1040.0)            # cold -> TP2 fires at +12
        assert [x.kind for x in d] == ["TP2"]

    def test_hot_regime_lifts_tp2_to_18(self):
        heat_regime.reset()
        for _ in range(25):                        # warm the window ALL hot (reach20=1.0)
            heat_regime.record_close(30.0)
        assert heat_regime.is_high() is True
        pm = _pm(regime_runner_lift=True, tp2_pct_hot=18.0, min_hold_floor_secs=0.0)
        p = _open(pm)
        _tick(pm, 6.5, now=1020.0)                 # TP1, stamps heat_high_at_entry=True
        assert p.state_blob.get("heat_high_at_entry") is True
        assert _tick(pm, 12.5, now=1040.0) == []   # +12 < lifted +18 -> NO TP2 yet
        d = _tick(pm, 18.5, now=1060.0)            # reaches the lifted target
        assert [x.kind for x in d] == ["TP2"]
        assert "heat-lift" in d[0].reason

    def test_lift_off_when_flag_off(self):
        heat_regime.reset()
        for _ in range(25):
            heat_regime.record_close(30.0)
        pm = _pm(regime_runner_lift=False, min_hold_floor_secs=0.0)
        _open(pm)
        _tick(pm, 6.5, now=1020.0)
        d = _tick(pm, 12.5, now=1040.0)            # flag off -> TP2 at +12
        assert [x.kind for x in d] == ["TP2"]


class TestHeatRegimeModule:
    def test_thin_history_is_cold(self):
        heat_regime.reset()
        heat_regime.record_close(30.0)             # only 1 fill (< MIN_FILLS)
        assert heat_regime.is_high() is False
        assert heat_regime.reach20_roll() == 0.0

    def test_threshold(self):
        heat_regime.reset()
        # 25 fills, 6 hot -> 0.24 >= 0.20 HIGH
        for i in range(25):
            heat_regime.record_close(30.0 if i < 6 else -5.0)
        assert heat_regime.reach20_roll() == pytest.approx(6 / 25)
        assert heat_regime.is_high() is True
        heat_regime.reset()
        for i in range(25):
            heat_regime.record_close(30.0 if i < 4 else -5.0)  # 0.16 < 0.20 LOW
        assert heat_regime.is_high() is False

    def test_mode_off_disables(self, monkeypatch):
        heat_regime.reset()
        for _ in range(25):
            heat_regime.record_close(30.0)
        monkeypatch.setenv("HEAT_REGIME_MODE", "off")
        assert heat_regime.is_high() is False
