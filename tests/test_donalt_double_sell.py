# tests/test_donalt_double_sell.py
"""DONALT 2026-07-06 11:07:50 double-sell race (live probe, real money).

tp1-fastfill ENFORCE sold the TP1 partial (0.75) but never set p.tp1_hit —
tick()'s contract sets the tier flag BEFORE the sell (per_bot_position_manager
~909). The next regular tick saw tp1_hit=False at pnl>=tp1 and fired TP1 AGAIN;
the duplicate live sell hit the full-leg clamp (sold_frac >= remaining) and
sized the ENTIRE pre-partial stack (113,794 tokens) off a lagging RPC balance
read that pre-dated the first fill. Only the on-chain "Insufficient funds"
revert stopped an oversell.

Two fixes under test:
1. fastfill enforce mirrors tick()'s TP1 contract (tp1_hit + peel stamps).
2. STALE-BALANCE guard: within 120s of a prior live sell leg on the mint, a
   sizing >1.5x the paper-expected slice = stale read -> stay open, retry.
"""
import asyncio
import time
import types

import pytest

from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager
from feeds.dip_scanner import DipScanner


MINT = "4eKYoR1hBHnRaYyg2d57H36uUatRNyZr1NRP9ScNpump"


def _mk_pm(bot_id="badday_young_absorb_live", **cfg_over):
    cfg = BotConfig(bot_id=bot_id, display_name=bot_id, tp1_pct=6.0,
                    tp1_sell_fraction=0.75, tp2_pct=15.0, trail_pp=2.0,
                    hard_stop_pct=-12.0, **cfg_over)
    pm = PerBotPositionManager(cfg)
    p = pm.open_position(token="DONALT", entry_price=0.0002,
                         size_usd=25.0, entry_time=900.0, address=MINT)
    return pm, p


def _mk_scanner_stub(pm, prices_above_tp1):
    """Duck-typed stand-in for DipScanner in _reprice_tp1_fastfill."""
    stub = types.SimpleNamespace()
    stub.bot_position_managers = {pm.config.bot_id: pm}
    from collections import deque
    stub._fast_samples = {MINT: deque(prices_above_tp1, maxlen=20)}
    stub.sells = []

    async def _fake_sell(bot_id, token, decision, price, now):
        stub.sells.append((bot_id, token, decision, price))

    async def _fake_batch(addrs):
        return {}

    stub._execute_bot_sell = _fake_sell
    stub._fast_batch_prices = _fake_batch
    stub._append_exit_reprice_shadow = lambda rec: None
    return stub


def _run_fastfill(stub, now=1000.0):
    cfg = types.SimpleNamespace(sample_window=20)
    asyncio.run(DipScanner._reprice_tp1_fastfill(
        stub, cfg, {MINT.lower(): stub._fast_samples[MINT][-1]}, now))


class TestFastfillTierContract:
    def test_enforce_sets_tp1_hit_before_sell(self, monkeypatch):
        monkeypatch.setenv("TP1_FASTFILL_MODE", "enforce")
        pm, p = _mk_pm()
        # fresh samples confirmed above the +6 line (entry 0.0002 -> +7.8%)
        stub = _mk_scanner_stub(pm, [0.0002 * 1.07, 0.0002 * 1.078])
        _run_fastfill(stub)
        assert len(stub.sells) == 1 and stub.sells[0][2].kind == "TP1"
        assert p.tp1_hit is True
        assert p.state_blob.get("tp1_ff_fired") is True
        assert p.state_blob.get("tp1_fill_pnl") == pytest.approx(7.8, abs=0.2)

    def test_regular_tick_cannot_double_fire_tp1(self, monkeypatch):
        """The DONALT race: after a fastfill TP1, the next tick at the same
        price must NOT emit a second TP1 decision."""
        monkeypatch.setenv("TP1_FASTFILL_MODE", "enforce")
        pm, p = _mk_pm()
        stub = _mk_scanner_stub(pm, [0.0002 * 1.07, 0.0002 * 1.078])
        _run_fastfill(stub)
        decisions = pm.tick("DONALT", 0.0002 * 1.078, now=1003.0)
        assert not any(d.kind == "TP1" for d in decisions)

    def test_enforce_stamps_peel_on_sub_threshold_fill(self, monkeypatch):
        monkeypatch.setenv("TP1_FASTFILL_MODE", "enforce")
        pm, p = _mk_pm(bot_id="badday_flush_peel_ab", peel_exit=True,
                       peel_threshold_pct=12.0)
        stub = _mk_scanner_stub(pm, [0.0002 * 1.07, 0.0002 * 1.078])
        _run_fastfill(stub)
        assert p.state_blob.get("peel_active") is True

    def test_enforce_no_peel_stamp_on_wick_fill(self, monkeypatch):
        monkeypatch.setenv("TP1_FASTFILL_MODE", "enforce")
        pm, p = _mk_pm(bot_id="badday_flush_peel_ab", peel_exit=True,
                       peel_threshold_pct=12.0)
        stub = _mk_scanner_stub(pm, [0.0002 * 1.14, 0.0002 * 1.15])
        _run_fastfill(stub)
        assert not p.state_blob.get("peel_active")


class _SwapReached(Exception):
    """Sentinel: execution got past sizing into the actual swap."""


def _mk_live_sell_stub(bal_atomic, recent_sell_mono=None):
    stub = types.SimpleNamespace()
    stub._live_sell_last_mono = (
        {MINT: recent_sell_mono} if recent_sell_mono is not None else {})

    async def _decimals(mint):
        return 6

    async def _balance(mint):
        return bal_atomic

    async def _swap(*a, **k):
        raise _SwapReached()

    stub.trader = types.SimpleNamespace(
        _get_token_decimals=_decimals,
        _get_token_balance_atomic=_balance,
        _execute_swap_ultra=_swap,
        _get_sol_balance=None,  # replaced below
    )

    async def _sol_bal(force=False):
        return 1.0

    stub.trader._get_sol_balance = _sol_bal
    return stub


def _mk_pos(remaining=0.25):
    return types.SimpleNamespace(
        address=MINT, remaining_fraction=remaining, size_usd=25.0,
        entry_price=0.00021462, state_blob={}, strategy="badday_young_absorb_live",
        pair_address=None)


class TestStaleBalanceGuard:
    def test_stale_read_after_partial_stays_open(self):
        """The exact DONALT numbers: wallet held ~28.4k tokens but the RPC
        served the pre-partial 113.88k -> full-leg would sell everything.
        Guard must return None (stay open) before any swap is attempted."""
        stub = _mk_live_sell_stub(bal_atomic=113_882_268_776,
                                  recent_sell_mono=time.monotonic())
        res = asyncio.run(DipScanner._execute_bot_sell_live(
            stub, "DONALT", None, _mk_pos(remaining=0.25), 0.25, 0.0002292))
        assert res is None

    def test_fresh_read_proceeds_to_swap(self):
        """A balance consistent with the paper-expected slice must NOT trip
        the guard even inside the recent-sell window."""
        stub = _mk_live_sell_stub(bal_atomic=28_448_612_342,
                                  recent_sell_mono=time.monotonic())
        with pytest.raises(_SwapReached):
            asyncio.run(DipScanner._execute_bot_sell_live(
                stub, "DONALT", None, _mk_pos(remaining=0.25), 0.25, 0.0002292))

    def test_no_recent_sell_proceeds_to_swap(self):
        """Outside the window the E1a chain-truth sizing stands untouched
        (transfer-tax / suspect-entry divergence is legitimate there)."""
        stub = _mk_live_sell_stub(bal_atomic=113_882_268_776,
                                  recent_sell_mono=None)
        with pytest.raises(_SwapReached):
            asyncio.run(DipScanner._execute_bot_sell_live(
                stub, "DONALT", None, _mk_pos(remaining=1.0), 1.0, 0.0002292))


# ── PRE-SETTLEMENT guard (TESTPACK 2026-07-07 orphan + fantasy-fill) ──────────
# A live SELL firing within the settlement window of the BUY reads a stale,
# pre-settlement balance (bought tokens not landed). TESTPACK: bought 81,198
# tokens, a sell 2s later read ~225 tokens, sold DUST + closed the book while
# the real bag bled -58% orphaned. Also: a 0-balance read at buy-time booked a
# paper-close-EMPTY at a glitch +79% price (fantasy paper win). Both must now
# STAY OPEN and retry.
TP_MINT = MINT
_TP_TOKENS = 81_198.486522          # bag actually bought for $25
_TP_ENTRY = 25.0 / _TP_TOKENS       # -> _paper_tok ~= 81,198 on a full leg


def _mk_tp_pos(remaining=1.0):
    return types.SimpleNamespace(
        address=TP_MINT, remaining_fraction=remaining, size_usd=25.0,
        entry_price=_TP_ENTRY, state_blob={}, strategy="badday_young_absorb_live",
        pair_address=None)


def _stub_with_buy(bal_atomic, buy_secs_ago=2.0):
    stub = _mk_live_sell_stub(bal_atomic=bal_atomic, recent_sell_mono=None)
    stub._live_buy_last_mono = {TP_MINT: time.monotonic() - buy_secs_ago}
    return stub


class TestPreSettlementGuard:
    def test_undersell_within_buy_window_stays_open(self):
        """The exact TESTPACK orphan: held 81,198 but the read served ~225
        (pre-settlement). Guard must return None (stay open) — never dust-sell."""
        stub = _stub_with_buy(bal_atomic=225_170_882)   # ~225 tokens, 6 dp
        res = asyncio.run(DipScanner._execute_bot_sell_live(
            stub, "TESTPACK", None, _mk_tp_pos(remaining=1.0), 1.0, _TP_ENTRY))
        assert res is None

    def test_zero_balance_within_buy_window_stays_open(self):
        """Bug A: a 0-balance read 0s after buy must NOT paper-close-empty
        (that booked the +79% fantasy). Stay open, retry."""
        stub = _stub_with_buy(bal_atomic=0, buy_secs_ago=0.5)
        res = asyncio.run(DipScanner._execute_bot_sell_live(
            stub, "TESTPACK", None, _mk_tp_pos(remaining=1.0), 1.0, _TP_ENTRY))
        assert res is None    # NOT {"empty": True}

    def test_full_balance_within_buy_window_proceeds(self):
        """A settled, correct balance inside the window must still sell — the
        guard triggers on STALE (far-below) reads, not on legitimate ones."""
        stub = _stub_with_buy(bal_atomic=81_198_486_522)   # the real bag
        with pytest.raises(_SwapReached):
            asyncio.run(DipScanner._execute_bot_sell_live(
                stub, "TESTPACK", None, _mk_tp_pos(remaining=1.0), 1.0, _TP_ENTRY))

    def test_undersell_outside_buy_window_proceeds(self):
        """No recent buy -> a small balance is a real (mostly-sold/drained)
        position; sell what's there rather than trap it forever."""
        stub = _mk_live_sell_stub(bal_atomic=225_170_882, recent_sell_mono=None)
        # no _live_buy_last_mono -> not pre-settlement
        with pytest.raises(_SwapReached):
            asyncio.run(DipScanner._execute_bot_sell_live(
                stub, "TESTPACK", None, _mk_tp_pos(remaining=1.0), 1.0, _TP_ENTRY))

    def test_zero_balance_no_recent_buy_closes_empty(self):
        """Genuinely empty (no recent buy) still returns empty so the caller
        can close — must NOT retry forever (the 2026-06-14 inflight clog)."""
        stub = _mk_live_sell_stub(bal_atomic=0, recent_sell_mono=None)
        res = asyncio.run(DipScanner._execute_bot_sell_live(
            stub, "TESTPACK", None, _mk_tp_pos(remaining=1.0), 1.0, _TP_ENTRY))
        assert res == {"empty": True}


# ── DOUBLE-BUY guard (TESTPACK 2026-07-07: two $25 bags bought 3s apart) ──────
# The "already held?" check read the on-chain balance, still 0 because buy #1
# hadn't settled -> both fired. _claim_live_buy is a synchronous in-flight set +
# book check, immune to settlement lag.
import types as _types


def _mk_scanner():
    return DipScanner.__new__(DipScanner)


class _PMHeld:
    def __init__(self, held): self._held = held
    def get_position(self, token): return object() if self._held else None


def _dec(addr="MINTX", token="TOK"):
    return _types.SimpleNamespace(address=addr, token=token)


class TestDoubleBuyGuard:
    def test_first_claim_succeeds(self):
        s = _mk_scanner()
        ok, key = s._claim_live_buy("bot1", _dec(), _PMHeld(False))
        assert ok is True and key in s._live_buy_inflight

    def test_second_concurrent_claim_rejected(self):
        """The exact race: claim #1 in-flight (not yet discarded), claim #2 for
        the SAME token must be rejected — no second swap."""
        s = _mk_scanner()
        ok1, _ = s._claim_live_buy("bot1", _dec(), _PMHeld(False))
        ok2, _ = s._claim_live_buy("bot1", _dec(), _PMHeld(False))
        assert ok1 is True and ok2 is False

    def test_claim_rejected_when_already_held(self):
        """Buy #1 already settled + registered in the book -> reject (belt &
        suspenders with the in-flight set)."""
        s = _mk_scanner()
        ok, _ = s._claim_live_buy("bot1", _dec(), _PMHeld(True))
        assert ok is False

    def test_discard_frees_the_slot(self):
        """After the first buy completes (finally: discard), a later buy for the
        same token is allowed again (legit re-entry, subject to the reentry cap)."""
        s = _mk_scanner()
        ok1, key = s._claim_live_buy("bot1", _dec(), _PMHeld(False))
        s._live_buy_inflight.discard(key)
        ok2, _ = s._claim_live_buy("bot1", _dec(), _PMHeld(False))
        assert ok1 is True and ok2 is True

    def test_different_tokens_independent(self):
        s = _mk_scanner()
        ok1, _ = s._claim_live_buy("bot1", _dec(addr="A", token="TA"), _PMHeld(False))
        ok2, _ = s._claim_live_buy("bot1", _dec(addr="B", token="TB"), _PMHeld(False))
        assert ok1 is True and ok2 is True
