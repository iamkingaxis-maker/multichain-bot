# tests/test_runner_scaled_trail.py
"""Peak-scaled runner trail (2026-07-06 EV model).

The strategy's edge is convex — a few runners carry the whole EV, so a FIXED
giveback (peel's 5pp) caps the exact tail that pays (a +70 runner cut at +65).
This trails tight below peak_ref and widens by k per pp of peak above it,
capped: peak +10 -> 5pp (exit +5); +40 -> 11pp (exit +29); +80 -> 19pp
(exit +61). Post-TP1 only; default off = byte-identical to the fixed trail.
"""
import os

from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager


def _pm(**over):
    base = dict(bot_id="t", display_name="t", tp1_pct=6.0, tp1_sell_fraction=0.30,
                tp2_pct=12.0, tp2_sell_fraction=0.25, trail_pp=2.0,
                hard_stop_pct=-12.0, peel_exit=True, peel_threshold_pct=12.0,
                runner_scaled_trail=True, runner_trail_base_pp=5.0,
                runner_trail_peak_ref_pp=10.0, runner_trail_k=0.2,
                runner_trail_cap_pp=20.0)
    base.update(over)
    return PerBotPositionManager(BotConfig(**base))


def _open_runner(pm, entry=1.0):
    """Open a position and drive it through TP1 (below peel threshold) so the
    peel runner arms."""
    pm.open_position(token="T", entry_price=entry, size_usd=25.0,
                     entry_time=900.0, address="mT")
    # TP1 fires at +6 (below +12 threshold) -> peel_active, 0.30 sold
    d = pm.tick("T", entry * 1.07, now=1000.0)
    assert any(x.kind == "TP1" for x in d)
    return pm.get_position("T")


def _run_to_peak_then_drop(pm, entry, peak_pct, drop_to_pct, t0=1100.0):
    """Tick up to peak_pct, then to drop_to_pct; return the exit decision list
    at the drop tick."""
    pm.tick("T", entry * (1 + peak_pct / 100.0), now=t0)          # set peak
    return pm.tick("T", entry * (1 + drop_to_pct / 100.0), now=t0 + 10)


class TestScaledGiveback:
    def test_small_gain_tight_trail(self):
        # peak +10 -> giveback = base 5pp -> exits at +5, holds at +6
        pm = _pm(); _open_runner(pm)
        assert not _run_to_peak_then_drop(pm, 1.0, 10.0, 6.0)   # +6 > +10-5 -> hold
        d = _run_to_peak_then_drop(pm, 1.0, 10.0, 4.0)          # +4 <= +5 -> exit
        assert any(x.kind == "POST_TP1_TRAIL" for x in d)

    def test_monster_breathes(self):
        # peak +80 -> giveback = 5 + 0.2*(80-10) = 19pp -> exit at +61.
        # A fixed 5pp trail would have exited at +75; scaled holds to +61.
        pm = _pm(); _open_runner(pm)
        assert not _run_to_peak_then_drop(pm, 1.0, 80.0, 65.0)  # +65 > +61 -> HOLD
        d = _run_to_peak_then_drop(pm, 1.0, 80.0, 60.0)         # +60 <= +61 -> exit
        assert any(x.kind == "POST_TP1_TRAIL" for x in d)
        assert "scaled-" in [x.reason for x in d if x.kind == "POST_TP1_TRAIL"][0]

    def test_cap_binds(self):
        # peak +200 -> 5 + 0.2*190 = 43 -> capped 20pp -> exit at +180
        pm = _pm(); _open_runner(pm)
        assert not _run_to_peak_then_drop(pm, 1.0, 200.0, 185.0)  # +185 > +180 hold
        d = _run_to_peak_then_drop(pm, 1.0, 200.0, 179.0)         # +179 <= +180 exit
        assert any(x.kind == "POST_TP1_TRAIL" for x in d)

    def test_mid_gain_interpolates(self):
        # peak +40 -> 5 + 0.2*30 = 11pp -> exit at +29
        pm = _pm(); _open_runner(pm)
        assert not _run_to_peak_then_drop(pm, 1.0, 40.0, 30.0)   # +30 > +29 hold
        d = _run_to_peak_then_drop(pm, 1.0, 40.0, 28.0)          # +28 <= +29 exit
        assert any(x.kind == "POST_TP1_TRAIL" for x in d)


class TestGatesAndSafety:
    def test_off_uses_fixed_trail(self):
        # scaled off -> peel_giveback_pp fixed 5pp regardless of peak
        pm = _pm(runner_scaled_trail=False, peel_giveback_pp=5.0)
        _open_runner(pm)
        # peak +80, drop to +73: fixed 5pp exits at +75, so +73 exits
        d = _run_to_peak_then_drop(pm, 1.0, 80.0, 73.0)
        assert any(x.kind == "POST_TP1_TRAIL" for x in d)
        assert "scaled-" not in " ".join(x.reason for x in d)

    def test_env_kill(self, monkeypatch):
        pm = _pm()
        _open_runner(pm)
        monkeypatch.setenv("RUNNER_SCALED_TRAIL_MODE", "off")
        # with scaling killed -> peel fixed 5pp -> peak +80 exits at +75
        d = _run_to_peak_then_drop(pm, 1.0, 80.0, 73.0)
        assert any(x.kind == "POST_TP1_TRAIL" for x in d)

    def test_hard_stop_still_floors(self):
        # a catastrophic drop still hits the -12 hard stop, not the runner trail
        pm = _pm()
        _open_runner(pm)
        d = pm.tick("T", 1.0 * (1 - 0.13), now=1100.0)   # -13% -> hard stop
        assert any("stop" in x.kind.lower() or "STOP" in x.reason.upper()
                   or x.kind == "HARD_STOP" for x in d) or any(
                   x.sell_fraction == 1.0 for x in d)
