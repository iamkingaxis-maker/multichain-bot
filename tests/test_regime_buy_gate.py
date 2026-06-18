# -*- coding: utf-8 -*-
"""Regime BUY-GATE (recalibrated 2026-06-18): block dip entries only on a clear crash.
Two arms: breadth>=40 outright; breadth in [35,40) only when SOL is also down on the day
(sol_pc_h24<=-1). Calibrated to the 4-week backtest; anti-overblock + fail-open."""
import os
import importlib
from core import regime_buy_gate as g


def test_breadth_cliff_blocks_at_40():
    # backtest: [40,50) = -6.93%/tr crash cliff -> block outright (any SOL)
    assert g.gate_blocks(40, 0.0)[0] is True
    assert g.gate_blocks(55, +5.0)[0] is True
    # below 40 with SOL not down -> NOT blocked (the two-sided 35-40 zone stays open)
    assert g.gate_blocks(39.9, 0.0)[0] is False
    assert g.gate_blocks(33, -2.0)[0] is False   # below MID entirely -> never blocked


def test_mid_breadth_blocks_only_on_red_day():
    # [35,40): block ONLY if SOL also down on the day (sol_h24<=-1)
    assert g.gate_blocks(37, -1.0)[0] is True     # mid-breadth + red day -> block
    assert g.gate_blocks(37, -5.0)[0] is True
    assert g.gate_blocks(37, -0.9)[0] is False    # mid-breadth but SOL ~flat -> allow (don't overblock)
    assert g.gate_blocks(37, +2.0)[0] is False
    assert g.gate_blocks(37, None)[0] is False    # mid-breadth, no SOL data -> conservative allow


def test_fail_open_on_missing_breadth():
    assert g.gate_blocks(None, None)[0] is False
    assert g.gate_blocks(None, -5.0)[0] is False
    assert g.gate_blocks("bad", "bad")[0] is False


def test_reason_strings():
    assert "breadth=40" in g.gate_blocks(40, 0.0)[1]
    r = g.gate_blocks(37, -2.0)[1]
    assert "breadth=37" in r and "sol_h24" in r


def test_verdict_enforced_only_in_enforce_mode():
    os.environ["REGIME_BUY_GATE_MODE"] = "shadow"
    importlib.reload(g)
    from core.regime_buy_gate import verdict as _v
    assert _v(50, 0.0)["block"] is True and _v(50, 0.0)["enforced"] is False
    os.environ["REGIME_BUY_GATE_MODE"] = "enforce"
    importlib.reload(g)
    from core.regime_buy_gate import verdict as _v2
    assert _v2(50, 0.0)["enforced"] is True
    assert _v2(20, 0.0)["enforced"] is False      # good regime -> not blocked
    assert _v2(37, -0.5)["enforced"] is False     # mid-breadth, SOL ~flat -> not blocked
    os.environ.pop("REGIME_BUY_GATE_MODE", None)
    importlib.reload(g)
