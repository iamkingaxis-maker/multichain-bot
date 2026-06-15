# -*- coding: utf-8 -*-
"""Liquidity-drain bail decision (Mechanism A, 2026-06-15) — the gap-through EXIT lever.

A rug drains LP as/just-before it craters the price; a sharp REMOVE verdict is a LEADING
indicator the price-reactive stop can't see (single-poll -66% gap-throughs). These cover the
pure decision; the shadow stamp wiring lives in dip_scanner._stamp_liq_drain_shadow."""
from feeds.liquidity_flow import drain_bail_decision


def _analysis(verdict, d5=None, samples=5):
    return {"lp_event_verdict": verdict, "lp_delta_5m_pct": d5, "lp_history_samples": samples}


def test_fires_on_remove_when_not_green():
    would, reason = drain_bail_decision(_analysis("REMOVE_5MIN", d5=-32.0), pnl_pct=-8.0)
    assert would is True
    assert "REMOVE_5MIN" in reason


def test_no_fire_when_stable():
    would, reason = drain_bail_decision(_analysis("STABLE", d5=-2.0), pnl_pct=-8.0)
    assert would is False
    assert reason == "stable"


def test_winner_safe_skips_green_position():
    # LP draining but we're solidly green (a holder exiting into strength) -> don't bail.
    would, reason = drain_bail_decision(_analysis("REMOVE_15MIN", d5=-40.0), pnl_pct=12.0)
    assert would is False
    assert "winner-safe" in reason


def test_winner_safe_boundary_just_below_green_fires():
    # pnl just under the winner-safe floor still bails (it's not green enough to protect).
    would, _ = drain_bail_decision(_analysis("REMOVE_5MIN", d5=-30.0), pnl_pct=2.9,
                                   winner_safe_pnl_min=3.0)
    assert would is True


def test_no_data_no_fire():
    assert drain_bail_decision(_analysis("REMOVE_5MIN", samples=0), pnl_pct=-10.0)[0] is False
    assert drain_bail_decision({}, pnl_pct=-10.0)[0] is False
    assert drain_bail_decision(_analysis(None, samples=5), pnl_pct=-10.0)[0] is False


def test_add_event_does_not_fire():
    # liquidity being ADDED is not a drain -> never bail on it.
    would, reason = drain_bail_decision(_analysis("ADD_5MIN", d5=+30.0), pnl_pct=-5.0)
    assert would is False
