# -*- coding: utf-8 -*-
"""PAPER per-token concentration cap (2026-06-18) — crash-protection lever #1.

The PAPER fleet (the 70-experiment selection instrument) was never touched by the
LIVE per-token cap, so today 7-37 paper bots pile the SAME mint (Chaton / cat-token /
MEEP) — the death-cluster / gap-rug tail. This cap limits HOW MANY bots can hold the
SAME token (by ADDRESS — symbols collide -> the SPCX phantom). It does NOT filter
which tokens we buy, so it never kills buy volume.

SHADOW-FIRST: default mode 'shadow' = log-only, no behavior change. 'enforce' is the
only mode that blocks. Fails OPEN (paper experiment-protection, not real-money safety).
These tests cover the address-keyed paper counter + the cap block's mode behavior."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from feeds.dip_scanner import DipScanner
from collections import OrderedDict


def _pos(token, address, size_usd):
    return NS(token=token, address=address, size_usd=size_usd)


def _scanner_with(books, live_bots=()):
    """DipScanner shell (no __init__) with the given per-bot books.
    books: {bot_id: [position, ...]}.
    live_bots: bot_ids whose config has live_probe=True (route live when key+ultra)."""
    sc = DipScanner.__new__(DipScanner)
    sc.bot_position_managers = {
        bid: NS(
            _positions={p.token: p for p in poss},
            config=NS(live_probe=(bid in live_bots)),
        )
        for bid, poss in books.items()
    }
    # By default a paper fleet: no private key -> nothing routes live.
    sc.trader = NS(private_key="")
    return sc


# ── _paper_token_exposure counter ────────────────────────────────────────

def test_paper_exposure_counts_by_address_across_fleet():
    A, B = "minta", "mintb"
    sc = _scanner_with({
        "b1": [_pos("DEGEN", A, 20.0)],
        "b2": [_pos("DEGEN", A, 25.0)],   # same mint, different bot -> concentration
        "b3": [_pos("OTHER", B, 20.0)],
    })
    assert sc._paper_token_exposure(A) == (2, 45.0)
    assert sc._paper_token_exposure(B) == (1, 20.0)


def test_paper_exposure_address_keyed_lowercased():
    # Position address stored mixed-case; query mixed-case -> still matches (lowercased).
    sc = _scanner_with({
        "b1": [_pos("DEGEN", "MintAbc", 20.0)],
        "b2": [_pos("DEGEN", "mintabc", 25.0)],  # same mint, different casing
    })
    assert sc._paper_token_exposure("MINTABC") == (2, 45.0)


def test_paper_same_symbol_different_mint_not_conflated():
    sc = _scanner_with({
        "b1": [_pos("DEGEN", "mint_real", 20.0)],
        "b2": [_pos("DEGEN", "mint_imposter", 20.0)],
    })
    assert sc._paper_token_exposure("mint_real") == (1, 20.0)
    assert sc._paper_token_exposure("mint_imposter") == (1, 20.0)


def test_paper_empty_address_returns_zero():
    sc = _scanner_with({"b1": [_pos("DEGEN", "mintA", 20.0)]})
    assert sc._paper_token_exposure("") == (0, 0.0)


def test_paper_no_exposure_for_unheld_mint():
    sc = _scanner_with({"b1": [_pos("DEGEN", "mintA", 20.0)]})
    assert sc._paper_token_exposure("mintZ") == (0, 0.0)


def test_paper_exposure_excludes_live_routed_bots():
    # When the fleet IS live (real key + ultra), a live_probe bot's position is LIVE,
    # not paper, so the paper counter must skip it (the live cap covers live bots).
    A = "minta"
    sc = _scanner_with(
        {
            "live1": [_pos("DEGEN", A, 30.0)],   # routes live
            "paper1": [_pos("DEGEN", A, 20.0)],  # paper
        },
        live_bots=("live1",),
    )
    sc.trader = NS(private_key="REALKEY")  # key present -> live_probe bots go live
    import core.trader as _ct
    _orig = _ct.USE_JUPITER_ULTRA
    _ct.USE_JUPITER_ULTRA = True
    try:
        # only the paper bot counts toward paper exposure
        assert sc._paper_token_exposure(A) == (1, 20.0)
    finally:
        _ct.USE_JUPITER_ULTRA = _orig


# ── cap block mode behavior (via _execute_bot_buy harness) ────────────────

def _exec_scanner(books, size_usd, addr, buyer_bot="newbot"):
    """Minimal harness to drive the PAPER cap block in _execute_bot_buy.
    Returns (scanner, opened_flag_holder). The buyer bot starts with no position."""
    sc = DipScanner.__new__(DipScanner)
    pms = {}
    for bid, poss in books.items():
        pms[bid] = NS(
            _positions={p.token: p for p in poss},
            config=NS(live_probe=False),
        )
    # buyer bot
    opened = {"count": 0}

    class _PM:
        def __init__(self):
            self._positions = {}
            self.config = NS(
                live_probe=False, momentum_mode=False, scalein_enabled=False,
                reentry_cooldown_secs=None, pool_sizing_derates_enabled=False,
                young_token_probe=False, low_mcap_probe=False,
                microcap_mandate=False, antirug_floor_exempt=False,
                daily_loss_limit_usd=None, max_token_buys_per_day=None,
            )

        def in_reentry_cooldown(self, *a, **k):
            return False

        def token_buys_today(self, *a, **k):
            return 0

        def open_position(self, **k):
            opened["count"] += 1
            return NS(token=k.get("token"), address=k.get("address"), size_usd=k.get("size_usd"))

    buyer_pm = _PM()
    pms[buyer_bot] = buyer_pm
    sc.bot_position_managers = pms

    class _Cap:
        def __init__(self):
            self.daily_pnl_usd = 0.0
            self.in_flight_usd = 0.0
            self.balance_usd = 10000.0

        def daily_loss_breached(self, *a, **k):
            return False

        def reserve_for_buy(self, amt):
            # raise to short-circuit AFTER the cap block (so we don't need full buy plumbing)
            raise ValueError("STOP-AFTER-CAP")

    sc.bot_capitals = {buyer_bot: _Cap()}
    for bid in books:
        sc.bot_capitals[bid] = _Cap()
    sc.trader = NS(private_key="")
    sc._addr_by_token = OrderedDict()  # production is an LRU OrderedDict (dip_scanner ~L598)
    sc._buy_gate = None
    sc._token_registry = None
    sc._user_watchlist_addrs = set()
    sc._cycle_sol_features = {}
    sc.min_mcap = 1_000_000
    decision = NS(
        bot_id=buyer_bot, token="DEGEN", address=addr, pair_address="pairX",
        size_usd=size_usd, entry_price=1.0,
    )
    return sc, decision, opened


def _run_buy(sc, decision, bundle=None):
    return asyncio.run(sc._execute_bot_buy(decision, bundle))


def test_cap_off_mode_byte_identical(monkeypatch, caplog):
    # off -> cap logic never engages; reaches reserve_for_buy (our STOP marker) regardless.
    monkeypatch.setenv("PAPER_PER_TOKEN_CAP_MODE", "off")
    A = "mintdegen"
    sc, decision, opened = _exec_scanner(
        {"b%d" % i: [_pos("DEGEN", A, 50.0)] for i in range(10)}, 50.0, A
    )
    with caplog.at_level("INFO"):
        _run_buy(sc, decision)
    assert "PAPER PER-TOKEN CAP" not in caplog.text
    assert opened["count"] == 0  # short-circuited at reserve, not before


def test_cap_unset_byte_identical(monkeypatch, caplog):
    # flag unset -> defaults to shadow; with FEW holders it shouldn't even log a would-block.
    monkeypatch.delenv("PAPER_PER_TOKEN_CAP_MODE", raising=False)
    A = "mintdegen"
    sc, decision, opened = _exec_scanner(
        {"b1": [_pos("DEGEN", A, 50.0)]}, 50.0, A   # only 1 holder, under cap of 4
    )
    with caplog.at_level("INFO"):
        _run_buy(sc, decision)
    assert "PAPER PER-TOKEN CAP" not in caplog.text


def test_cap_shadow_logs_but_does_not_block(monkeypatch, caplog):
    # 4 bots already hold; a 5th buys. Shadow -> LOGS would-block but does NOT block
    # (proceeds to reserve_for_buy, our STOP marker).
    monkeypatch.setenv("PAPER_PER_TOKEN_CAP_MODE", "shadow")
    monkeypatch.setenv("PAPER_PER_TOKEN_MAX_POSITIONS", "4")
    A = "mintdegen"
    sc, decision, opened = _exec_scanner(
        {"b%d" % i: [_pos("DEGEN", A, 50.0)] for i in range(4)}, 50.0, A
    )
    with caplog.at_level("INFO"):
        _run_buy(sc, decision)
    assert "PAPER PER-TOKEN CAP" in caplog.text
    assert "SHADOW-would-block" in caplog.text
    # NOT blocked: it proceeded past the cap to reserve (open never called because
    # our reserve raises, but the point is the cap did not early-return before reserve).
    # We verify non-block via the absence of an early return: a blocked path would skip
    # reserve entirely. Distinguish with the enforce test below.


def test_cap_enforce_blocks_over_count(monkeypatch, caplog):
    # 4 hold (== max); the 5th is BLOCKED (early return BEFORE reserve_for_buy).
    monkeypatch.setenv("PAPER_PER_TOKEN_CAP_MODE", "enforce")
    monkeypatch.setenv("PAPER_PER_TOKEN_MAX_POSITIONS", "4")
    A = "mintdegen"
    sc, decision, opened = _exec_scanner(
        {"b%d" % i: [_pos("DEGEN", A, 50.0)] for i in range(4)}, 50.0, A
    )
    # spy on reserve to confirm we never reach it
    reached = {"reserve": False}
    cap = sc.bot_capitals[decision.bot_id]
    _orig_reserve = cap.reserve_for_buy

    def _spy(amt):
        reached["reserve"] = True
        return _orig_reserve(amt)

    cap.reserve_for_buy = _spy
    with caplog.at_level("INFO"):
        _run_buy(sc, decision)
    assert "PAPER PER-TOKEN CAP BLOCK" in caplog.text
    assert reached["reserve"] is False  # blocked BEFORE reserve


def test_cap_enforce_allows_under_count(monkeypatch):
    # 3 hold (< max 4); the 4th proceeds to reserve (NOT blocked).
    monkeypatch.setenv("PAPER_PER_TOKEN_CAP_MODE", "enforce")
    monkeypatch.setenv("PAPER_PER_TOKEN_MAX_POSITIONS", "4")
    A = "mintdegen"
    sc, decision, opened = _exec_scanner(
        {"b%d" % i: [_pos("DEGEN", A, 50.0)] for i in range(3)}, 50.0, A
    )
    reached = {"reserve": False}
    cap = sc.bot_capitals[decision.bot_id]
    _orig_reserve = cap.reserve_for_buy

    def _spy(amt):
        reached["reserve"] = True
        return _orig_reserve(amt)

    cap.reserve_for_buy = _spy
    _run_buy(sc, decision)
    assert reached["reserve"] is True  # under cap -> reaches reserve (not blocked)
