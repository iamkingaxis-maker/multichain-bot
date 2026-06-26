"""Peak-anchored breakeven-lock gate (winner-comparison 2026-06-26).

A PRE-TP1 leg that confirmed green (peak >= +7%) then round-tripped to <=0 is a
give-back loser; lock ~breakeven instead of riding to the floor. Validated path-aware
(+349pp net, winner-kill 0.15 at peak>=7). Helper is pure + FAIL-SAFE; the gate is
shadow by default (record-only) and only emits an ExitDecision under enforce.
"""
import pytest
from core.bot_evaluator import breakeven_lock_fires as bel
from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager


# ---- helper unit tests ----
def test_fires_when_confirmed_green_then_breakeven():
    fire, why = bel(peak_pnl_pct=8.0, pnl_pct=-0.3, tp1_hit=False)
    assert fire is True and "breakeven-lock" in why


def test_no_fire_below_peak_min():
    assert bel(peak_pnl_pct=6.9, pnl_pct=-1.0, tp1_hit=False)[0] is False


def test_no_fire_while_still_green():
    assert bel(peak_pnl_pct=8.0, pnl_pct=0.5, tp1_hit=False)[0] is False


def test_no_fire_after_tp1():
    # post-TP1 the trail owns the exit; breakeven-lock must stand down
    assert bel(peak_pnl_pct=20.0, pnl_pct=-2.0, tp1_hit=True)[0] is False


def test_fires_exactly_at_zero_and_at_threshold():
    assert bel(peak_pnl_pct=7.0, pnl_pct=0.0, tp1_hit=False)[0] is True


def test_custom_peak_min():
    assert bel(peak_pnl_pct=10.0, pnl_pct=-1.0, tp1_hit=False, peak_min=15.0)[0] is False
    assert bel(peak_pnl_pct=16.0, pnl_pct=-1.0, tp1_hit=False, peak_min=15.0)[0] is True


def test_fail_safe_on_bad_input():
    assert bel(peak_pnl_pct=None, pnl_pct=-1.0, tp1_hit=False)[0] is False
    assert bel(peak_pnl_pct=float("nan"), pnl_pct=-1.0, tp1_hit=False)[0] is False
    assert bel(peak_pnl_pct=8.0, pnl_pct="x", tp1_hit=False)[0] is False


# ---- tick integration ----
def _cfg(**ov):
    base = dict(bot_id="b1", display_name="Bot 1", tp1_pct=50.0, hard_stop_pct=-90.0)
    base.update(ov)
    return BotConfig(**base)


def _peak_then_breakeven(pm):
    pm.open_position("TOK", 1.0, 20.0, entry_time=0.0)
    pm.tick(token="TOK", current_price=1.08, now=60.0)   # peak +8%
    return pm.tick(token="TOK", current_price=1.00, now=120.0)  # round-trip to ~0


def test_enforce_emits_breakeven_lock_exit(monkeypatch):
    monkeypatch.setenv("BREAKEVEN_LOCK_MODE", "enforce")
    d = _peak_then_breakeven(PerBotPositionManager(_cfg()))
    assert any(x.kind == "BREAKEVEN_LOCK" and x.sell_fraction == 1.0 for x in d)


def test_shadow_default_records_but_does_not_exit(monkeypatch):
    monkeypatch.setenv("BREAKEVEN_LOCK_MODE", "shadow")
    pm = PerBotPositionManager(_cfg())
    d = _peak_then_breakeven(pm)
    assert not any(x.kind == "BREAKEVEN_LOCK" for x in d)   # no behavior change in shadow
    sb = pm.get_position("TOK").state_blob or {}
    assert sb.get("bel_shadow_fired") is True               # but it IS measured


def test_off_does_not_record_or_exit(monkeypatch):
    monkeypatch.setenv("BREAKEVEN_LOCK_MODE", "off")
    pm = PerBotPositionManager(_cfg())
    d = _peak_then_breakeven(pm)
    assert not any(x.kind == "BREAKEVEN_LOCK" for x in d)
    sb = pm.get_position("TOK").state_blob or {}
    assert not sb.get("bel_shadow_fired")
