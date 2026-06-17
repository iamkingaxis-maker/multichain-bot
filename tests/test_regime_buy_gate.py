# -*- coding: utf-8 -*-
"""Regime BUY-GATE (2026-06-17): binary don't-buy on a clear crash, calibrated to the
4-week backtest (breadth>=35 cliff) + anti-overblock (no block in the breakeven 30-35
zone; 6h SOL; fail-open on missing data)."""
import os
import importlib
from core import regime_buy_gate as g


def test_breadth_cliff_blocks_at_35_not_below():
    # backtest: [30,35) is breakeven (+0.2%) -> must NOT block (overblock guard)
    assert g.gate_blocks(33, 0.0)[0] is False     # today-like ~33% breadth: not blocked by breadth arm
    assert g.gate_blocks(34.9, 0.0)[0] is False
    # [35,40)=-2.3%, [40,50)=-10.7% -> block
    assert g.gate_blocks(35, 0.0)[0] is True
    assert g.gate_blocks(48, 0.0)[0] is True


def test_sol_arm_blocks_hard_crash_only():
    assert g.gate_blocks(10, -3.0)[0] is True      # SOL 6h -3% = hard crash
    assert g.gate_blocks(10, -5.0)[0] is True
    assert g.gate_blocks(10, -2.9)[0] is False     # mild SOL dip -> not blocked (no overblock)
    assert g.gate_blocks(10, +1.0)[0] is False


def test_fail_open_on_missing_features():
    # missing data must NEVER block (worst overblock would be halting on a data gap)
    assert g.gate_blocks(None, None)[0] is False
    assert g.gate_blocks(None, -1.0)[0] is False
    assert g.gate_blocks(10, None)[0] is False
    assert g.gate_blocks("bad", "bad")[0] is False


def test_or_logic_and_reason():
    blk, reason = g.gate_blocks(40, -4.0)
    assert blk is True and "breadth" in reason and "sol_h6" in reason


def test_verdict_enforced_only_in_enforce_mode():
    os.environ["REGIME_BUY_GATE_MODE"] = "shadow"
    importlib.reload(g)
    v = g.gate_blocks(50, -5.0)
    assert v[0] is True
    from core.regime_buy_gate import verdict as _v
    assert _v(50, -5.0)["enforced"] is False       # shadow: block computed, not enforced
    os.environ["REGIME_BUY_GATE_MODE"] = "enforce"
    importlib.reload(g)
    from core.regime_buy_gate import verdict as _v2
    assert _v2(50, -5.0)["enforced"] is True
    assert _v2(20, 0.0)["enforced"] is False       # good regime: not enforced/blocked
    os.environ.pop("REGIME_BUY_GATE_MODE", None)
    importlib.reload(g)
