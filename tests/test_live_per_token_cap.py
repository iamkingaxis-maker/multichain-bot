# -*- coding: utf-8 -*-
"""Live per-token fleet exposure cap (2026-06-15) — the DEGEN-x198 / 05-23 guard.

The catastrophic equal-weight drawdown (-$5k on 05-23) was MANY bots holding the
SAME token when it rugged (198 live positions in one mint). The cap counts the
fleet's concurrent LIVE exposure in one token, by ADDRESS (symbols collide -> the
SPCX phantom), and blocks a live buy that would pile on. LIVE-money only; paper is
untouched. These tests cover the address-keyed counter that drives it.

2026-06-21 semantics change (verified against dip_scanner._live_token_exposure):
the counter includes ONLY positions of bots that ROUTE LIVE (should_route_live:
config.live_probe + USE_JUPITER_ULTRA + real private key). Before that fix the
~70 paper bots piling one mint tripped the LIVE cap and shut the live bot out
(badday_flush_nf15_live blocked on OGFLOKI by phantom paper exposure). The
harness therefore builds a LIVE-routing fleet; paper exclusion has its own test.
"""
import pytest
from types import SimpleNamespace as NS
from feeds.dip_scanner import DipScanner


@pytest.fixture(autouse=True)
def _ultra_on(monkeypatch):
    # should_route_live requires USE_JUPITER_ULTRA; pin it True so the fleet
    # below counts as live. (Module-level constant read at call time via
    # `from core.trader import USE_JUPITER_ULTRA` inside the method.)
    import core.trader as trader_mod
    monkeypatch.setattr(trader_mod, "USE_JUPITER_ULTRA", True)


def _scanner_with(books, live=True, has_key=True):
    """Build a DipScanner shell (no __init__) with the given per-bot books.
    books: {bot_id: [position, ...]}. live/has_key control whether the bots
    count as LIVE-routing for _live_token_exposure."""
    sc = DipScanner.__new__(DipScanner)
    sc.trader = NS(private_key="testkey" if has_key else "")
    sc.bot_position_managers = {
        bid: NS(
            config=NS(live_probe=live),
            _positions={p.token: p for p in poss},
        )
        for bid, poss in books.items()
    }
    return sc


def _pos(token, address, size_usd):
    return NS(token=token, address=address, size_usd=size_usd)


def test_exposure_counts_by_address_across_fleet():
    A, B = "mintA", "mintB"
    sc = _scanner_with({
        "b1": [_pos("DEGEN", A, 20.0)],
        "b2": [_pos("DEGEN", A, 25.0)],   # same mint, different bot -> concentration
        "b3": [_pos("OTHER", B, 20.0)],
    })
    assert sc._live_token_exposure(A) == (2, 45.0)
    assert sc._live_token_exposure(B) == (1, 20.0)


def test_same_symbol_different_mint_not_conflated():
    # Two tokens share the ticker "DEGEN" but are different mints — must NOT be
    # counted together (the address-keying requirement; symbol-keying = SPCX bug).
    sc = _scanner_with({
        "b1": [_pos("DEGEN", "mint_real", 20.0)],
        "b2": [_pos("DEGEN", "mint_imposter", 20.0)],
    })
    assert sc._live_token_exposure("mint_real") == (1, 20.0)
    assert sc._live_token_exposure("mint_imposter") == (1, 20.0)


def test_empty_address_returns_zero():
    sc = _scanner_with({"b1": [_pos("DEGEN", "mintA", 20.0)]})
    assert sc._live_token_exposure("") == (0, 0.0)


def test_no_exposure_for_unheld_mint():
    sc = _scanner_with({"b1": [_pos("DEGEN", "mintA", 20.0)]})
    assert sc._live_token_exposure("mintZ") == (0, 0.0)


def test_paper_bots_not_counted_by_live_cap():
    """2026-06-21 fix under test: PAPER positions (bots without live_probe) must
    NOT trip the LIVE per-token cap — that phantom exposure blocked the single
    live bot (OGFLOKI '7 live pos/$925' that were all paper)."""
    A = "mintA"
    sc = _scanner_with({
        "p1": [_pos("DEGEN", A, 100.0)],
        "p2": [_pos("DEGEN", A, 100.0)],
    }, live=False)
    assert sc._live_token_exposure(A) == (0, 0.0)


def test_no_private_key_counts_nothing():
    """No real key -> nothing routes live -> the LIVE cap sees zero exposure
    (fail-open on the counter is safe: without a key no live buy can fire)."""
    A = "mintA"
    sc = _scanner_with({"b1": [_pos("DEGEN", A, 20.0)]}, has_key=False)
    assert sc._live_token_exposure(A) == (0, 0.0)
