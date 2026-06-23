"""Conviction SOL-red down-size (badday gap audit 2026-06-22, ENFORCE).

When a trigger_count conviction bot would up-size (>1x) AND SOL is falling
(sol_pc_h1 <= -0.3), cap the size to 1x. SIZE-DOWN only — never blocks.
"""
import pytest
from core.bot_config import BotConfig
from core.bot_evaluator import BotEvaluator


def _ev():
    cfg = BotConfig(bot_id="conv", display_name="conv",
                    conviction_sizing_mode="trigger_count",
                    conviction_step=0.5, conviction_max_mult=2.0)
    return BotEvaluator(cfg)


class _B:
    def __init__(self, sol_pc_h1):
        self.sol_pc_h1 = sol_pc_h1


def test_upsizes_normally_when_sol_not_red():
    ev = _ev()
    # 3 triggers -> mult = 1 + 0.5*2 = 2.0; SOL flat -> full up-size
    base, tag = ev._apply_conviction(100.0, ("a", "b", "c"), _B(0.0))
    assert base == pytest.approx(200.0) and "x2.00" in tag


def test_downsizes_when_sol_red(monkeypatch):
    monkeypatch.setenv("CONVICTION_SOLRED_MODE", "enforce")
    ev = _ev()
    base, tag = ev._apply_conviction(100.0, ("a", "b", "c"), _B(-0.5))
    assert base == pytest.approx(100.0) and "solred" in tag


def test_boundary_minus_03_downsizes(monkeypatch):
    monkeypatch.setenv("CONVICTION_SOLRED_MODE", "enforce")
    ev = _ev()
    assert ev._apply_conviction(100.0, ("a", "b"), _B(-0.3))[0] == pytest.approx(100.0)
    # just above the threshold -> still up-sizes (mult for 2 triggers = 1.5)
    assert ev._apply_conviction(100.0, ("a", "b"), _B(-0.29))[0] == pytest.approx(150.0)


def test_no_downsize_when_mult_is_1x(monkeypatch):
    # a single trigger -> mult 1.0 (not an up-size) -> SOL-red irrelevant, no change
    monkeypatch.setenv("CONVICTION_SOLRED_MODE", "enforce")
    ev = _ev()
    base, tag = ev._apply_conviction(100.0, ("a",), _B(-0.9))
    assert base == pytest.approx(100.0) and "solred" not in tag


def test_mode_off_disables(monkeypatch):
    monkeypatch.setenv("CONVICTION_SOLRED_MODE", "off")
    ev = _ev()
    base, _ = ev._apply_conviction(100.0, ("a", "b", "c"), _B(-0.9))
    assert base == pytest.approx(200.0)


def test_fail_open_on_missing_sol(monkeypatch):
    monkeypatch.setenv("CONVICTION_SOLRED_MODE", "enforce")
    ev = _ev()
    # no bundle / no sol -> no down-size (can't confirm red) -> normal up-size
    assert ev._apply_conviction(100.0, ("a", "b", "c"), None)[0] == pytest.approx(200.0)
    assert ev._apply_conviction(100.0, ("a", "b", "c"), _B(None))[0] == pytest.approx(200.0)
