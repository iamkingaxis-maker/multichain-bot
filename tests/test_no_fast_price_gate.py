"""Tests for the no-fast-price entry gate (2026-06-18).

AxiS: tokens we can't FAST-price (Jupiter isn't returning a fresh sample) should
not be traded. The fast-watch arms ~457 tokens but Jupiter only polls ~353 (77%);
the other ~23% have no fresh fast price. This gate flags entries on those tokens.

SHADOW-FIRST: default mode 'shadow' = log-only (no behavior change). 'enforce'
blocks the buy. FAIL-OPEN: any gate error allows the buy (inverse of a safety cap
— we'd rather trade than wrongly block on a bug, especially in shadow).
"""
import asyncio
import time
import types
from collections import deque, OrderedDict

import pytest

from feeds.dip_scanner import DipScanner


# ── _has_fresh_fast_price ────────────────────────────────────────────────────

def _scanner_with_samples():
    s = DipScanner.__new__(DipScanner)
    s._fast_samples = {}
    s._fast_samples_ts = {}
    s._fast_armed = {}
    return s


def test_fresh_sample_is_priceable():
    s = _scanner_with_samples()
    addr = "ABCdef123"
    s._fast_samples[addr] = deque([1.0, 1.1], maxlen=8)
    s._fast_samples_ts[addr] = time.time()
    assert s._has_fresh_fast_price(addr) is True


def test_no_sample_is_not_priceable():
    s = _scanner_with_samples()
    assert s._has_fresh_fast_price("NeverSeen") is False


def test_empty_deque_is_not_priceable():
    s = _scanner_with_samples()
    addr = "EmptyTok"
    s._fast_samples[addr] = deque(maxlen=8)
    s._fast_samples_ts[addr] = time.time()
    assert s._has_fresh_fast_price(addr) is False


def test_stale_sample_is_not_priceable(monkeypatch):
    s = _scanner_with_samples()
    addr = "StaleTok"
    s._fast_samples[addr] = deque([2.0], maxlen=8)
    # last update 999s ago, well past the 30s default
    s._fast_samples_ts[addr] = time.time() - 999.0
    assert s._has_fresh_fast_price(addr) is False


def test_case_insensitive_lookup():
    s = _scanner_with_samples()
    # _fast_samples is original-case keyed (FIX5)
    addr = "MixEdCaseAddr"
    s._fast_samples[addr] = deque([1.0], maxlen=8)
    s._fast_samples_ts[addr] = time.time()
    assert s._has_fresh_fast_price(addr.lower()) is True
    assert s._has_fresh_fast_price(addr.upper()) is True


def test_respects_max_age_env(monkeypatch):
    s = _scanner_with_samples()
    addr = "AgeTok"
    s._fast_samples[addr] = deque([1.0], maxlen=8)
    s._fast_samples_ts[addr] = time.time() - 20.0  # 20s old
    monkeypatch.setenv("NO_FAST_PRICE_MAX_AGE_SECS", "10")
    assert s._has_fresh_fast_price(addr) is False  # 20 > 10
    monkeypatch.setenv("NO_FAST_PRICE_MAX_AGE_SECS", "60")
    assert s._has_fresh_fast_price(addr) is True   # 20 < 60


def test_no_timestamp_falls_back_to_presence():
    """If _fast_samples_ts is missing/empty (e.g. pre-deploy state), a non-empty
    deque is treated as priceable (presence-only fallback, no false block)."""
    s = DipScanner.__new__(DipScanner)
    s._fast_samples = {"PresentTok": deque([1.0], maxlen=8)}
    # no _fast_samples_ts attribute at all
    assert s._has_fresh_fast_price("PresentTok") is True


# ── _execute_bot_buy gate branch ─────────────────────────────────────────────

def _gate_scanner(mode, armed_addr, priceable):
    """Minimal scanner exercising ONLY the no-fast-price gate branch of
    _execute_bot_buy. We stub everything the buy path needs UP TO the gate and
    make reserve_for_buy raise a sentinel so we can detect 'the buy proceeded
    past the gate' without running the full fire path."""
    s = DipScanner.__new__(DipScanner)
    s._fast_samples = {}
    s._fast_samples_ts = {}
    s._fast_armed = {}
    s._addr_by_token = OrderedDict()  # production is an LRU OrderedDict (dip_scanner ~L598)
    s._nfp_stats = {"would_block": 0, "priceable": 0, "by_bot": {}}

    addr = armed_addr
    # The token is ARMED (we're trying to poll it) either way — that's the
    # warmup-safe condition. priceable controls whether Jupiter returned a sample.
    s._fast_armed[addr] = {"pair": "P"}
    if priceable:
        s._fast_samples[addr] = deque([1.0], maxlen=8)
        s._fast_samples_ts[addr] = time.time()

    bot_id = "botX"

    # capital: reserve_for_buy raises sentinel -> means we got PAST every gate.
    class _Sentinel(Exception):
        pass

    class _Cap:
        daily_pnl_usd = 0.0
        in_flight_usd = 0.0
        def daily_loss_breached(self, *a, **k):
            return False
        def reserve_for_buy(self, *a, **k):
            raise _Sentinel()

    class _PMConfig:
        momentum_mode = False
        live_probe = False
        reentry_cooldown_secs = None
        daily_loss_limit_usd = None
        max_token_buys_per_day = None
        young_token_probe = False
        low_mcap_probe = False
        microcap_mandate = False
        antirug_floor_exempt = False
        pool_sizing_derates_enabled = False

    class _PM:
        config = _PMConfig()
        def in_reentry_cooldown(self, *a, **k):
            return False
        def token_buys_today(self, *a, **k):
            return 0

    s.bot_capitals = {bot_id: _Cap()}
    s.bot_position_managers = {bot_id: _PM()}
    s.trader = types.SimpleNamespace(private_key="")  # paper (no key) -> not live
    s._buy_gate = None
    s._token_registry = None
    s._user_watchlist_addrs = set()
    s._cycle_sol_features = {}
    s.min_mcap = 1_000_000

    decision = types.SimpleNamespace(
        bot_id=bot_id, token="TKN", address=addr, pair_address="P",
        entry_price=1.0, size_usd=20.0, size_tier="t", triggers_fired=(),
        reason_summary="r",
    )
    bundle = types.SimpleNamespace(raw_meta={}, liquidity_usd=100_000.0,
                                   mcap_usd=2_000_000.0, pc_h1=None,
                                   shape_90m_drawdown_from_max_pct=None)
    return s, decision, bundle, _Sentinel


def _ran_to_reserve(s, decision, bundle, sentinel):
    """Returns True if execution reached reserve_for_buy (= buy proceeded past
    the gate), False if it returned earlier (= gate blocked)."""
    try:
        asyncio.run(s._execute_bot_buy(decision, bundle))
    except sentinel:
        return True
    except Exception:
        # any OTHER exception means we got past the gate but blew up downstream;
        # for these tests reserve_for_buy is the FIRST thing past the gate.
        raise
    return False


def test_gate_off_unpriceable_proceeds(monkeypatch):
    monkeypatch.setenv("NO_FAST_PRICE_GATE_MODE", "off")
    s, d, b, sent = _gate_scanner("off", "armedAddr", priceable=False)
    assert _ran_to_reserve(s, d, b, sent) is True
    assert s._nfp_stats["would_block"] == 0  # gate did not run


def test_gate_shadow_unpriceable_proceeds_but_counts(monkeypatch):
    monkeypatch.setenv("NO_FAST_PRICE_GATE_MODE", "shadow")
    s, d, b, sent = _gate_scanner("shadow", "armedAddr", priceable=False)
    assert _ran_to_reserve(s, d, b, sent) is True  # shadow does NOT block
    assert s._nfp_stats["would_block"] == 1
    assert s._nfp_stats["by_bot"]["botX"]["block"] == 1


def test_gate_enforce_unpriceable_blocks(monkeypatch):
    monkeypatch.setenv("NO_FAST_PRICE_GATE_MODE", "enforce")
    s, d, b, sent = _gate_scanner("enforce", "armedAddr", priceable=False)
    assert _ran_to_reserve(s, d, b, sent) is False  # enforce BLOCKS
    assert s._nfp_stats["would_block"] == 1


def test_gate_enforce_priceable_proceeds(monkeypatch):
    monkeypatch.setenv("NO_FAST_PRICE_GATE_MODE", "enforce")
    s, d, b, sent = _gate_scanner("enforce", "armedAddr", priceable=True)
    assert _ran_to_reserve(s, d, b, sent) is True  # priceable -> not blocked
    assert s._nfp_stats["priceable"] == 1
    assert s._nfp_stats["by_bot"]["botX"]["ok"] == 1


def test_gate_shadow_not_armed_does_not_block(monkeypatch):
    """Warmup safety: a token NOT in _fast_armed (brand-new, never watched) is
    NOT flagged would-block — we only flag tokens we ARE trying to poll."""
    monkeypatch.setenv("NO_FAST_PRICE_GATE_MODE", "enforce")
    s, d, b, sent = _gate_scanner("enforce", "armedAddr", priceable=False)
    # de-arm the token: now it's brand-new / unknown to the fast watch
    s._fast_armed = {}
    assert _ran_to_reserve(s, d, b, sent) is True  # not armed -> proceed
    assert s._nfp_stats["would_block"] == 0


def test_gate_fail_open(monkeypatch):
    """A bug in the gate must NEVER block the buy. Force _has_fresh_fast_price to
    raise; the buy must proceed in enforce mode (fail-open)."""
    monkeypatch.setenv("NO_FAST_PRICE_GATE_MODE", "enforce")
    s, d, b, sent = _gate_scanner("enforce", "armedAddr", priceable=False)
    def boom(*a, **k):
        raise RuntimeError("gate bug")
    s._has_fresh_fast_price = boom
    assert _ran_to_reserve(s, d, b, sent) is True  # fail-open -> proceed
