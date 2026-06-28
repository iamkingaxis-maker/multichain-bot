# -*- coding: utf-8 -*-
"""END-TO-END proof harness for the MAIN_SCAN_BUY_MODE=arm_only fresh-price route.

We flipped MAIN_SCAN_BUY_MODE=arm_only in prod: the main scan only ARMS a
would-buy token (it never commits a buy on the ~2-min-stale DexScreener snapshot);
the fast-watch tick re-decides on a FRESH Jupiter price ~3s later and fires the
buy. Since the flip SOL has trended down so every legit candidate was filtered →
no buy has yet *completed* through arm_only in prod. This harness PROVES the
routing chain fires end-to-end, independent of market conditions.

It drives the REAL code chain (no re-implementation):

    _fast_watch_tick            (~feeds/dip_scanner.py:5381)
      -> _eval_one_survivor     (nested ~5544; patches the FRESH price into the pair)
        -> _evaluate_pair       (~5760; the ~15k-line per-token eval + routing block)
          -> _fast_route_decisions (~4741)
            -> _execute_bot_buy  (~1369; terminal buy executor)

ONLY two seams are stubbed (per the spec): the DECISION SOURCE
(bot_manager.evaluate_all / evaluate_all_async — so the ~96 known-good filters
don't gate the routing proof) and the TERMINAL swap (we spy on _execute_bot_buy
to record the call + the price it was handed). Every routing method above runs
for real. The fast-tick IO seams that hit the network (batch price fetch, exit-
reprice, telemetry) are stubbed because they are NOT part of the routing chain
under test; the fresh price is injected through the real price-injection path
(_fast_batch_prices -> _fast_samples -> _eval_one_survivor patch).

PAPER/dry only — no real money, no network.

Run: python -m pytest tests/test_arm_only_buy_route_e2e.py -v
"""
import asyncio
import os
import sys
import time
from collections import deque
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import feeds.dip_scanner as ds_mod
from feeds.dip_scanner import DipScanner
from core.bot_evaluator import BuyDecision

BOT_ID = "deepflush_timebox"           # an enabled-style dip bot id
ADDR = "MintArmOnlyProofAaaBbbCccDddEeeFffGggHhhpump"  # base58-ish, non-stable
SYMBOL = "ARMPROOF"

STALE_PX = 0.00010                     # ~2-min-stale main-scan snapshot price
FRESH_PX = 0.00013                     # the fresh Jupiter price the fast tick sees


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _fake_bot_manager(decision_factory):
    """A BotManager stand-in whose evaluate_all/_async return a PASSING decision
    built from the bundle it receives (so the decision tracks the bundle's fresh
    price). enabled_bot_ids/has_momentum_bot cover the fast-path allowlist +
    prescreen seams."""

    def evaluate_all(bundle, realized_pnl_by_bot=None, bot_allowlist=None):
        return [decision_factory(bundle)]

    async def evaluate_all_async(bundle, realized_pnl_by_bot=None,
                                 bot_allowlist=None, yield_every=15):
        return [decision_factory(bundle)]

    return NS(
        evaluate_all=evaluate_all,
        evaluate_all_async=evaluate_all_async,
        enabled_bot_ids=lambda: frozenset({BOT_ID}),
        has_momentum_bot=lambda allow=None: False,
    )


def _decision_from_bundle(bundle):
    """Build a passing BuyDecision whose entry_price is the bundle's price — i.e.
    the price the REAL _evaluate_pair derived from the (fresh-patched) pair."""
    return BuyDecision(
        bot_id=BOT_ID,
        token=SYMBOL,
        address=ADDR,
        pair_address="pairARMPROOF",
        entry_price=float(bundle.price_usd),
        size_usd=30.0,
        size_tier="base",
        triggers_fired=("user_watchlist_bypass",),
        reason_summary="e2e arm_only routing proof",
    )


def _benign_pair(price_usd):
    """A pair dict crafted to survive EVERY top-level continue-gate in
    _evaluate_pair (baseline_mode + user-watchlist + pc_h24<=-3 carry the rest).
    pc_h24=-4.0: <= -3 (1s-eligibility for the trigger gate) yet > -5 (dodges the
    red_h24 gate)."""
    now_ms = int(time.time() * 1000)
    return {
        "baseToken": {"address": ADDR, "symbol": SYMBOL},
        "pairAddress": "pairARMPROOF",
        "priceUsd": str(price_usd),
        "marketCap": 5_000_000,                 # within [min_mcap, max_mcap]
        "pairCreatedAt": now_ms - 30 * 86_400 * 1000,   # 30d old (> 7d min_age)
        "liquidity": {"usd": 80_000.0},
        "volume": {"h24": 1_000_000.0, "h6": 300_000.0,
                   "h1": 60_000.0, "m5": 5_000.0},
        "priceChange": {"h24": -4.0, "h6": -2.0, "h1": -1.0, "m5": -0.5},
        "txns": {
            "h24": {"buys": 800, "sells": 600},
            "h6": {"buys": 300, "sells": 200},
            "h1": {"buys": 80, "sells": 50},
            "m5": {"buys": 8, "sells": 5},       # total 13 > 4 (freshness gate)
        },
    }


def _make_scanner(decision_factory):
    """DipScanner shell with the minimal REAL object graph + benign stubs for the
    non-routing seams. Built via __new__ so no network/__init__ side effects."""
    sc = DipScanner.__new__(DipScanner)

    # ── config scalars (real __init__ defaults) ──────────────────────────────
    sc.min_mcap = 1_000_000
    sc.max_mcap = 100_000_000
    sc.min_age_ms = int(7 * 86_400 * 1000)
    sc.min_volume_h24 = 200_000
    sc.max_concurrent = 3
    sc.min_txn_ratio_h6 = 1.3
    sc.min_vol_h1_ratio = 0.5
    sc.require_vol_m5 = True
    sc.min_turnover_h24 = 2.0
    sc.baseline_mode = True                 # relax directional continue-gates
    sc.position_usd = 500.0

    sc._h24_history_window_secs = 6 * 3600
    sc._h24_reversal_threshold = 0.25
    sc._h24_reversal_min_samples = 3
    sc._h24_reversal_min_peak = 100.0

    # ── state containers ─────────────────────────────────────────────────────
    sc._user_watchlist_addrs = {ADDR.lower()}   # bypass discovery gates + inject trigger
    sc.open_positions_ref = {}
    sc._cycle_trend_reversal_blocked = []
    sc._h24_history = {}
    sc._h24_history_dirty = False
    sc._rejected_distribution = 0
    sc._fp_shadow_culled = set()
    sc._scan_prefetch_cache = {}
    sc._jup_slip_cache = {}
    sc._jup_slip_ttl = 90.0
    sc._cycle_sol_features = {}
    sc._last_buy_time = 0.0
    sc.signals_fired = 0
    sc.tokens_evaluated = 0
    sc._addr_by_token = {}
    sc._filter_shadow_buf = []
    sc._filter_shadow_buf_max = 5000
    sc._token_registry = None
    sc.axiom_price_feed = None              # dereferenced in entry_meta build -> None is safe

    # fast-watch state
    sc._fast_armed = {}
    sc._fast_force_eval = {}
    sc._fast_samples = {}
    sc._fast_samples_ts = {}
    sc._fast_watch_regime = {}
    sc._buy_fire_lock = asyncio.Lock()

    # ── collaborators ────────────────────────────────────────────────────────
    sc.trader = NS(private_key="")          # paper; no is_dip_in_cooldown attr -> hasattr False
    sc.gt_client = None
    sc.dexs_client = None
    sc._smart_money = None
    sc._dev_wallet = None
    sc.bot_manager = _fake_bot_manager(decision_factory)
    sc.bot_capitals = {BOT_ID: NS(realized_pnl_total_usd=0.0)}

    # ── non-routing seam stubs (NOT under test) ──────────────────────────────
    sc._fast_price_for = lambda addr, jup: (jup, "jupiter")
    sc._fast_stash_seen_price = lambda *a, **k: None
    sc._fw_record_tick = lambda *a, **k: None
    sc._fw_record_hit = lambda *a, **k: None

    async def _fake_batch_prices(addrs):
        # the fresh-price source the real tick reads + buffers into _fast_samples
        return {ADDR.lower(): FRESH_PX}
    sc._fast_batch_prices = _fake_batch_prices

    async def _fake_reprice_exit_floors(cfg, prices, now):
        return None
    sc._reprice_exit_floors = _fake_reprice_exit_floors

    return sc


def _spy_execute_bot_buy(sc):
    """Replace the terminal buy executor with a recorder. Returns the call log."""
    calls = []

    async def _spy(decision, bundle):
        calls.append({"decision": decision, "bundle": bundle})
    sc._execute_bot_buy = _spy
    return calls


def _base_env(monkeypatch, main_scan_mode):
    # MULTI_BOT_ENABLED is read at module import -> patch the module global.
    monkeypatch.setattr(ds_mod, "MULTI_BOT_ENABLED", True)
    monkeypatch.setenv("MAIN_SCAN_BUY_MODE", main_scan_mode)
    monkeypatch.setenv("PAPER_MODE", "true")
    monkeypatch.setenv("HEAVY_EVAL_PRESCREEN", "off")    # never prescreen-skip the fan-out
    monkeypatch.setenv("NO_FAST_PRICE_GATE_MODE", "off")
    monkeypatch.setenv("RT_TRIGGER_MODE", "off")         # keep snapshot priceChange (pc_h24=-4)
    monkeypatch.setenv("RT_ARM_MODE", "off")             # no re-arm in the tick
    monkeypatch.setenv("RT_DEMAND_TURN_MODE", "off")
    monkeypatch.setenv("FAST_WATCH_PINNED_PRICE", "off")


# =========================================================================
# TEST 1 — arm_only fires a buy on the FRESH path (the core proof)
# =========================================================================

def test_arm_only_fires_buy_on_fresh_price_via_fast_watch_tick(monkeypatch):
    """arm_only + FAST_WATCH_MODE=enforce: an armed token is force-evaluated by the
    REAL fast-watch tick on the FRESH price and the buy ROUTES to _execute_bot_buy
    with the fresh price (not the stale main-scan snapshot)."""
    _base_env(monkeypatch, "arm_only")
    monkeypatch.setenv("FAST_WATCH_MODE", "enforce")

    sc = _make_scanner(_decision_from_bundle)
    calls = _spy_execute_bot_buy(sc)

    # ARM a synthetic token exactly as the main-scan->arm path does: the armed
    # pair carries the STALE snapshot price; force-eval queues a fresh re-decide.
    armed_pair = _benign_pair(STALE_PX)
    now = time.time()
    sc._fast_armed[ADDR] = armed_pair
    sc._fast_force_eval[ADDR] = now

    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    assert cfg.mode == "enforce"
    dedup = FastWatchDedup(cfg.eval_cooldown_secs)

    _run(sc._fast_watch_tick(cfg, dedup))

    # ── PROOF ────────────────────────────────────────────────────────────────
    assert len(calls) == 1, (
        f"arm_only did NOT route a buy through the fresh fast-watch path "
        f"(_execute_bot_buy calls={len(calls)})"
    )
    rec = calls[0]
    assert rec["decision"].address == ADDR
    assert rec["decision"].bot_id == BOT_ID
    # The bundle the REAL _evaluate_pair built carries the FRESH price (derived
    # from the fresh-patched pair), NOT the stale main-scan snapshot.
    assert rec["bundle"].price_usd == pytest.approx(FRESH_PX), (
        f"buy fired on the WRONG price: bundle.price_usd={rec['bundle'].price_usd} "
        f"expected FRESH {FRESH_PX} (stale was {STALE_PX})"
    )
    assert rec["bundle"].price_usd != pytest.approx(STALE_PX)
    # The decision entry price the routing carried is the fresh price too.
    assert rec["decision"].entry_price == pytest.approx(FRESH_PX)


# =========================================================================
# TEST 2 — control: MAIN_SCAN_BUY_MODE=on buys directly on the main-scan path
# =========================================================================

def test_main_scan_on_buys_directly(monkeypatch):
    """Legacy path intact: with MAIN_SCAN_BUY_MODE=on the main-scan eval
    (_fast_path_allowlist=None) routes the passing decision straight to
    _execute_bot_buy (no arming)."""
    _base_env(monkeypatch, "on")

    sc = _make_scanner(_decision_from_bundle)
    calls = _spy_execute_bot_buy(sc)

    pair = _benign_pair(STALE_PX)
    ctx = {
        "now_ms": int(time.time() * 1000),
        "_regime_n": 0,
        "_regime_dip_breadth_pct": 0.0,
        "_regime_h1_neg_pct": 0.0,
        "_fast_cache_only_charts": True,        # no cold chart/network fetch
        # no _fast_path_allowlist key -> _fp_allow is None -> MAIN-SCAN path
    }
    _run(sc._evaluate_pair(pair, ctx))

    assert len(calls) == 1, (
        f"MAIN_SCAN_BUY_MODE=on must buy directly on the main-scan path "
        f"(_execute_bot_buy calls={len(calls)})"
    )
    assert calls[0]["decision"].address == ADDR
    # Nothing was armed on the 'on' path.
    assert ADDR not in sc._fast_armed


# =========================================================================
# TEST 3 — arm gate: arm_only does NOT buy on the main-scan path, it ARMS
# =========================================================================

def test_arm_only_main_scan_arms_not_buys(monkeypatch):
    """The gate: with MAIN_SCAN_BUY_MODE=arm_only the main-scan eval
    (_fast_path_allowlist=None) must NOT commit a buy on the stale price — it
    ARMS the would-buy token + queues a force-eval for the fresh path instead."""
    _base_env(monkeypatch, "arm_only")

    sc = _make_scanner(_decision_from_bundle)
    calls = _spy_execute_bot_buy(sc)

    pair = _benign_pair(STALE_PX)
    ctx = {
        "now_ms": int(time.time() * 1000),
        "_regime_n": 0,
        "_regime_dip_breadth_pct": 0.0,
        "_regime_h1_neg_pct": 0.0,
        "_fast_cache_only_charts": True,
        # main-scan path (_fp_allow None)
    }
    _run(sc._evaluate_pair(pair, ctx))

    assert len(calls) == 0, (
        f"arm_only must NOT buy on the stale main-scan price, but "
        f"_execute_bot_buy was called {len(calls)} time(s)"
    )
    # Instead the would-buy token is armed + force-eval queued for the fresh path.
    assert ADDR in sc._fast_armed, "arm_only must ARM the would-buy token"
    assert ADDR in sc._fast_force_eval, "arm_only must queue a fresh force-eval"
