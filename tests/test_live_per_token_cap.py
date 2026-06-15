# -*- coding: utf-8 -*-
"""Live per-token fleet exposure cap (2026-06-15) — the DEGEN-x198 / 05-23 guard.

The catastrophic equal-weight drawdown (-$5k on 05-23) was MANY bots holding the
SAME token when it rugged (198 live positions in one mint). The cap counts the
fleet's concurrent LIVE exposure in one token, by ADDRESS (symbols collide -> the
SPCX phantom), and blocks a live buy that would pile on. LIVE-money only; paper is
untouched. These tests cover the address-keyed counter that drives it."""
from types import SimpleNamespace as NS
from feeds.dip_scanner import DipScanner


def _scanner_with(books):
    """Build a DipScanner shell (no __init__) with the given per-bot books.
    books: {bot_id: [position, ...]}."""
    sc = DipScanner.__new__(DipScanner)
    sc.bot_position_managers = {
        bid: NS(_positions={p.token: p for p in poss}) for bid, poss in books.items()
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
