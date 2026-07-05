# -*- coding: utf-8 -*-
"""ARM_INSTANT_FIRE — proof the on-arm instant COLD fresh-price fire resumes the
dark dip fleet.

Background: under MAIN_SCAN_BUY_MODE=arm_only the main scan only ARMS a would-buy
token (never buys on the ~2-min-stale snapshot). The fast-watch eval runs
cache_only=True, which strips the chart m1_features + recent_trades, so the
chart/order-flow ENTRY TRIGGERS never fire and `_evaluate_pair` bails at the
no-triggers gate BEFORE the multi-bot fan-out / evaluate_all -> 0 buys fleet-wide
(the 24h dark-fleet bug). The fix: on arm, schedule an off-loop instant eval that
runs COLD (cache_only=False) on a FRESH Jupiter price.

These tests drive the REAL trigger computation (NOT an always-pass evaluate_all
stub) to prove the cache_only difference: with cache_only=True the eval bails
before evaluate_all; with cache_only=False a real `confirmed_dip` trigger fires
and the buy routes on the FRESH price.

The ONLY stubbed seams: the chart assembler (feeds.chart_data.assemble_chart_data
-> a deterministic dip-shaped series), the decision source (bot_manager.evaluate_
all/_async, spied to confirm reach + return a passing decision), the fresh-price
fetch (_fast_batch_prices), and the terminal swap (_execute_bot_buy, spied). Every
routing method between trigger and buy runs for real.

PAPER/dry only — no real money, no network.

Run: python -m pytest tests/test_arm_instant_fresh_fire.py -v
"""
import asyncio
import os
import sys
import time
from collections import deque, OrderedDict
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import feeds.dip_scanner as ds_mod
import feeds.chart_data as cd_mod
from feeds.dip_scanner import DipScanner
from feeds.candle_utils import Candle
from core.bot_evaluator import BuyDecision

BOT_ID = "deepflush_timebox"
ADDR = "MintArmInstantFireAaaBbbCccDddEeeFffGggHhpump"
SYMBOL = "ARMFIRE"

STALE_PX = 0.00010
FRESH_PX = 0.00013


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _dip_chart():
    """A flat-bottom 1m series: vol_spike ~1.0 (>=0.40) and cum_3min 0% (>=-3) so
    m1_features satisfy `confirmed_dip`'s freshness gate. Same candles on every TF
    (only candles_1m feed the gates under test)."""
    cs = []
    t = 1_000_000
    for i in range(6):
        cs.append(Candle(open_time=t + i, open=1.0, high=1.02, low=0.98,
                         close=1.0, volume=100.0, close_time=t + i + 1))
    return cd_mod.ChartData(pool_address="pairARMFIRE", candles_1m=cs,
                            candles_5m=cs, candles_15m=cs, candles_1h=cs)


def _dip_pair(price_usd):
    """A pair crafted to (a) survive every discovery continue-gate WITHOUT the
    user-watchlist bypass and (b) fire ONLY the chart-dependent `confirmed_dip`
    trigger (pc_m5<=-5 AND pc_h1<=-15 AND m1 freshness). marketCap=3.5M dodges the
    mcap_psych_level trigger (>5% off every round level); pc_h24=-2.0 is >-3 so the
    token is NOT 1s-eligible-standalone (no chart-less fallback trigger) yet >-5 so
    it dodges the red_h24 gate. => no trigger fires without the chart."""
    now_ms = int(time.time() * 1000)
    return {
        "baseToken": {"address": ADDR, "symbol": SYMBOL},
        "pairAddress": "pairARMFIRE",
        "priceUsd": str(price_usd),
        "marketCap": 3_500_000,
        "pairCreatedAt": now_ms - 30 * 86_400 * 1000,
        "liquidity": {"usd": 80_000.0},
        "volume": {"h24": 1_000_000.0, "h6": 300_000.0,
                   "h1": 60_000.0, "m5": 5_000.0},
        "priceChange": {"h24": -2.0, "h6": -2.0, "h1": -16.0, "m5": -6.0},
        "txns": {
            "h24": {"buys": 800, "sells": 600},
            "h6": {"buys": 300, "sells": 200},
            "h1": {"buys": 80, "sells": 50},
            "m5": {"buys": 8, "sells": 5},
        },
    }


def _decision_from_bundle(bundle):
    return BuyDecision(
        bot_id=BOT_ID,
        token=SYMBOL,
        address=ADDR,
        pair_address="pairARMFIRE",
        entry_price=float(bundle.price_usd),
        size_usd=30.0,
        size_tier="base",
        triggers_fired=("confirmed_dip",),
        reason_summary="arm-instant-fire proof",
    )


def _make_scanner(user_watch=False):
    """DipScanner shell built via __new__ (no network/__init__ side effects).
    Returns (sc, eval_calls, buy_calls) where eval_calls records evaluate_all
    reach and buy_calls records the terminal _execute_bot_buy."""
    sc = DipScanner.__new__(DipScanner)

    sc.min_mcap = 1_000_000
    sc.max_mcap = 100_000_000
    sc.min_age_ms = int(7 * 86_400 * 1000)
    sc.min_volume_h24 = 200_000
    sc.max_concurrent = 3
    sc.min_txn_ratio_h6 = 1.3
    sc.min_vol_h1_ratio = 0.5
    sc.require_vol_m5 = True
    sc.min_turnover_h24 = 2.0
    sc.baseline_mode = True
    sc.position_usd = 500.0

    sc._h24_history_window_secs = 6 * 3600
    sc._h24_reversal_threshold = 0.25
    sc._h24_reversal_min_samples = 3
    sc._h24_reversal_min_peak = 100.0

    sc._user_watchlist_addrs = {ADDR.lower()} if user_watch else set()
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
    sc._addr_by_token = OrderedDict()  # production is an LRU OrderedDict (dip_scanner ~L598)
    sc._filter_shadow_buf = []
    sc._filter_shadow_buf_max = 5000
    sc._token_registry = None
    sc.axiom_price_feed = None

    sc._fast_armed = {}
    sc._fast_force_eval = {}
    sc._cycle_bought_addrs = set()
    sc._fast_samples = {}
    sc._fast_samples_ts = {}
    sc._fast_watch_regime = {}
    sc._buy_fire_lock = asyncio.Lock()

    sc.trader = NS(private_key="")
    sc.gt_client = None
    sc.dexs_client = None
    sc._smart_money = None
    sc._dev_wallet = None

    eval_calls = []

    def evaluate_all(bundle, realized_pnl_by_bot=None, bot_allowlist=None):
        eval_calls.append({"price": float(bundle.price_usd)})
        return [_decision_from_bundle(bundle)]

    async def evaluate_all_async(bundle, realized_pnl_by_bot=None,
                                 bot_allowlist=None, yield_every=15):
        return evaluate_all(bundle)

    sc.bot_manager = NS(
        evaluate_all=evaluate_all,
        evaluate_all_async=evaluate_all_async,
        enabled_bot_ids=lambda: frozenset({BOT_ID}),
        has_momentum_bot=lambda allow=None: False,
    )
    sc.bot_capitals = {BOT_ID: NS(realized_pnl_total_usd=0.0)}

    sc._fast_price_for = lambda addr, jup: (jup, "jupiter")
    sc._fast_stash_seen_price = lambda *a, **k: None
    sc._fw_record_tick = lambda *a, **k: None
    sc._fw_record_hit = lambda *a, **k: None

    async def _fake_batch_prices(addrs):
        return {ADDR.lower(): FRESH_PX}
    sc._fast_batch_prices = _fake_batch_prices

    async def _fake_reprice_exit_floors(cfg, prices, now):
        return None
    sc._reprice_exit_floors = _fake_reprice_exit_floors

    sc._fast_held_or_blocked = lambda a, allow=None: False

    buy_calls = []

    async def _spy_buy(decision, bundle):
        buy_calls.append({"decision": decision, "bundle": bundle})
    sc._execute_bot_buy = _spy_buy

    return sc, eval_calls, buy_calls


def _stub_chart(monkeypatch, chart):
    async def _fake_assemble(gt_client, pool_address, dexs_client=None):
        return chart
    monkeypatch.setattr(cd_mod, "assemble_chart_data", _fake_assemble)


def _base_env(monkeypatch):
    monkeypatch.setattr(ds_mod, "MULTI_BOT_ENABLED", True)
    monkeypatch.setenv("MAIN_SCAN_BUY_MODE", "arm_only")
    monkeypatch.setenv("PAPER_MODE", "true")
    monkeypatch.setenv("HEAVY_EVAL_PRESCREEN", "off")
    monkeypatch.setenv("NO_FAST_PRICE_GATE_MODE", "off")
    monkeypatch.setenv("RT_TRIGGER_MODE", "off")
    monkeypatch.setenv("RT_ARM_MODE", "off")
    monkeypatch.setenv("RT_DEMAND_TURN_MODE", "off")
    monkeypatch.setenv("RT_DIP_MODE", "off")
    monkeypatch.setenv("FAST_WATCH_PINNED_PRICE", "off")
    monkeypatch.setenv("FAST_WATCH_MODE", "enforce")
    monkeypatch.setenv("ARM_INSTANT_FIRE_MODE", "on")
    # Tests target the FLEET fan-out path; disable the separate LEGACY single-bot
    # engine so the main-scan eval doesn't fall through to it (force_paper'd, but
    # not part of the chain under test).
    monkeypatch.setenv("LEGACY_ENGINE_ENABLED", "false")


def _cfg_dedup():
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    dedup = FastWatchDedup(cfg.eval_cooldown_secs)
    return cfg, dedup


# =========================================================================
# TEST 1 — RED reproduction: cache_only=True strips triggers -> eval BAILS
#          before evaluate_all -> NO buy (the dark-fleet bug).
# =========================================================================

def test_cache_only_bails_before_decisions(monkeypatch):
    _base_env(monkeypatch)
    # No chart available in the fast path (cache_only=True never cold-fetches).
    _stub_chart(monkeypatch, None)

    sc, eval_calls, buy_calls = _make_scanner(user_watch=False)
    cfg, dedup = _cfg_dedup()
    pair = _dip_pair(FRESH_PX)
    prices = {ADDR.lower(): FRESH_PX}
    now = time.time()

    _run(sc._fast_eval_one(ADDR, pair, prices, now, int(now * 1000),
                           {}, hot_set={ADDR}, cache_only=True,
                           cfg=cfg, dedup=dedup))

    assert len(eval_calls) == 0, (
        "cache_only=True must BAIL at the no-triggers gate BEFORE evaluate_all "
        f"(evaluate_all reached {len(eval_calls)} time(s))"
    )
    assert len(buy_calls) == 0, "cache_only=True must NOT buy (no trigger fired)"


# =========================================================================
# TEST 2 — the fix: cache_only=False + dip chart -> a REAL trigger fires ->
#          buy routes on the FRESH price.
# =========================================================================

def test_cold_eval_fires_on_fresh_price(monkeypatch):
    _base_env(monkeypatch)
    _stub_chart(monkeypatch, _dip_chart())

    sc, eval_calls, buy_calls = _make_scanner(user_watch=False)
    cfg, dedup = _cfg_dedup()
    pair = _dip_pair(STALE_PX)          # armed pair carries the STALE price
    prices = {ADDR.lower(): FRESH_PX}   # the FRESH price the cold eval re-fetches
    now = time.time()

    _run(sc._fast_eval_one(ADDR, pair, prices, now, int(now * 1000),
                           {}, hot_set={ADDR}, cache_only=False,
                           cfg=cfg, dedup=dedup))

    # The precise behavioral difference vs test 1: the cold eval REACHES
    # evaluate_all (a real confirmed_dip trigger fired).
    assert len(eval_calls) == 1, (
        f"cache_only=False must REACH evaluate_all (got {len(eval_calls)})"
    )
    assert len(buy_calls) == 1, (
        f"cache_only=False must route exactly one buy (got {len(buy_calls)})"
    )
    rec = buy_calls[0]
    assert rec["decision"].address == ADDR
    assert rec["decision"].bot_id == BOT_ID
    # Fired on the FRESH price, never the stale main-scan snapshot.
    assert rec["bundle"].price_usd == pytest.approx(FRESH_PX), (
        f"fired on WRONG price bundle={rec['bundle'].price_usd} "
        f"expected FRESH {FRESH_PX} (stale was {STALE_PX})"
    )
    assert rec["bundle"].price_usd != pytest.approx(STALE_PX)
    assert eval_calls[0]["price"] == pytest.approx(FRESH_PX)


# =========================================================================
# TEST 3 — the arm path schedules an _arm_fresh_fire task and does NOT buy
#          on the stale main-scan price.
# =========================================================================

def test_arm_schedules_instant_fire(monkeypatch):
    _base_env(monkeypatch)

    sc, eval_calls, buy_calls = _make_scanner(user_watch=True)

    scheduled = []

    async def _spy_arm_fresh_fire(addr, pair):
        scheduled.append({"addr": addr, "pair": pair})
    sc._arm_fresh_fire = _spy_arm_fresh_fire

    # This test exercises ARM scheduling, not the chart trigger. Use a
    # 1s-eligible pair (pc_h24=-4 <= -3) so the user-watchlist bypass arms it
    # via the main-scan path without needing a cold chart (the cache_only
    # discriminator is covered by tests 1+2).
    pair = _dip_pair(STALE_PX)
    pair["priceChange"]["h24"] = -4.0
    ctx = {
        "now_ms": int(time.time() * 1000),
        "_regime_n": 0,
        "_regime_dip_breadth_pct": 0.0,
        "_regime_h1_neg_pct": 0.0,
        "_fast_cache_only_charts": True,
        # main-scan path (no _fast_path_allowlist -> _fp_allow None -> arm gate)
    }

    async def _drive():
        await sc._evaluate_pair(pair, ctx)
        # Let the off-loop create_task run to completion.
        if getattr(sc, "_arm_fire_tasks", None):
            await asyncio.gather(*list(sc._arm_fire_tasks))

    _run(_drive())

    assert len(buy_calls) == 0, "arm_only must NOT buy on the stale main-scan price"
    assert ADDR in sc._fast_armed, "arm_only must ARM the would-buy token"
    assert ADDR in sc._fast_force_eval, "arm_only must keep the drain fallback flag"
    assert len(scheduled) == 1, (
        f"arm must schedule exactly one _arm_fresh_fire (got {len(scheduled)})"
    )
    assert scheduled[0]["addr"] == ADDR


# =========================================================================
# TEST 4 — fail-open: no fresh price -> no raise, no buy, force-eval retained.
# =========================================================================

def test_arm_fresh_fire_failopen_no_price(monkeypatch):
    _base_env(monkeypatch)

    sc, eval_calls, buy_calls = _make_scanner(user_watch=False)

    async def _empty_prices(addrs):
        return {}
    sc._fast_batch_prices = _empty_prices

    now = time.time()
    sc._fast_armed[ADDR] = _dip_pair(STALE_PX)
    sc._fast_force_eval[ADDR] = now

    # Must not raise.
    _run(sc._arm_fresh_fire(ADDR, sc._fast_armed[ADDR]))

    assert len(buy_calls) == 0, "no fresh price -> no buy"
    assert len(eval_calls) == 0, "no fresh price -> eval not reached"
    assert ADDR in sc._fast_force_eval, (
        "no-price path must RETAIN _fast_force_eval[addr] for the drain fallback"
    )


# =========================================================================
# TEST 5 — no double-fire: arm once, run _arm_fresh_fire then a tick ->
#          exactly one _execute_bot_buy.
# =========================================================================

# =========================================================================
# TEST 6 — ChartMemo dedup (the fast-follow): the on-arm COLD eval
#          (cache_only=False) REUSES a warm ChartMemo entry the main scan
#          just wrote WITHOUT a redundant cold GT fetch (kills the 429 storm),
#          and STILL cold-fetches when the memo is genuinely absent.
# =========================================================================

def _spy_assemble(monkeypatch, chart):
    """Stub assemble_chart_data + count cold-fetch invocations."""
    calls = []

    async def _fake_assemble(gt_client, pool_address, dexs_client=None):
        calls.append(pool_address)
        return chart
    monkeypatch.setattr(cd_mod, "assemble_chart_data", _fake_assemble)
    return calls


def test_cold_eval_reuses_warm_memo_without_fetch(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("FEATURE_MEMO", "on")
    # The cold GT fetch is spied: a HIT must NOT call it.
    cold_calls = _spy_assemble(monkeypatch, _dip_chart())

    sc, eval_calls, buy_calls = _make_scanner(user_watch=False)
    cfg, dedup = _cfg_dedup()

    # Pre-populate the memo exactly as the MAIN scan would have, milliseconds ago
    # (well within the default 20s TTL): address-keyed, truthy dip chart.
    from core.fast_watch import ChartMemo
    sc._chart_memo = ChartMemo(20.0)
    sc._chart_memo.put(ADDR, _dip_chart(), time.monotonic())

    pair = _dip_pair(STALE_PX)
    prices = {ADDR.lower(): FRESH_PX}
    now = time.time()

    _run(sc._fast_eval_one(ADDR, pair, prices, now, int(now * 1000),
                           {}, hot_set={ADDR}, cache_only=False,
                           cfg=cfg, dedup=dedup))

    assert len(cold_calls) == 0, (
        "warm ChartMemo must SHORT-CIRCUIT the cold GT fetch on the on-arm path "
        f"(assemble_chart_data was called {len(cold_calls)} time(s) — the 429 storm)"
    )
    # The memo chart still drives a real trigger -> buy on the FRESH price.
    assert len(eval_calls) == 1, (
        f"warm-memo cold eval must still REACH evaluate_all (got {len(eval_calls)})"
    )
    assert len(buy_calls) == 1, (
        f"warm-memo cold eval must route exactly one buy (got {len(buy_calls)})"
    )
    assert buy_calls[0]["bundle"].price_usd == pytest.approx(FRESH_PX)


def test_cold_eval_fetches_when_memo_absent(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("FEATURE_MEMO", "on")
    cold_calls = _spy_assemble(monkeypatch, _dip_chart())

    sc, eval_calls, buy_calls = _make_scanner(user_watch=False)
    cfg, dedup = _cfg_dedup()

    # Empty memo (genuinely cold) — the on-arm path MUST still cold-fetch so the
    # entry triggers can fire (no regression of the dark-fleet fix).
    from core.fast_watch import ChartMemo
    sc._chart_memo = ChartMemo(20.0)

    pair = _dip_pair(STALE_PX)
    prices = {ADDR.lower(): FRESH_PX}
    now = time.time()

    _run(sc._fast_eval_one(ADDR, pair, prices, now, int(now * 1000),
                           {}, hot_set={ADDR}, cache_only=False,
                           cfg=cfg, dedup=dedup))

    assert len(cold_calls) == 1, (
        "absent ChartMemo must FALL THROUGH to exactly one cold GT fetch "
        f"(assemble_chart_data was called {len(cold_calls)} time(s))"
    )
    assert len(eval_calls) == 1, "cold-fetched chart must reach evaluate_all"
    assert len(buy_calls) == 1, "cold-fetched chart must route one buy"


def test_no_double_fire(monkeypatch):
    _base_env(monkeypatch)
    _stub_chart(monkeypatch, _dip_chart())

    sc, eval_calls, buy_calls = _make_scanner(user_watch=False)
    cfg, dedup = _cfg_dedup()
    # Share cfg/dedup with the on-arm path (Part 4) so the tick + instant fire
    # cooperate (shared dedup is a double-fire guard).
    sc._fast_cfg = cfg
    sc._fast_dedup = dedup

    pair = _dip_pair(STALE_PX)
    now = time.time()
    sc._fast_armed[ADDR] = pair
    sc._fast_force_eval[ADDR] = now

    async def _drive():
        # 1) Instant on-arm fire -> the single legitimate buy.
        await sc._arm_fresh_fire(ADDR, pair)
        # 2) A subsequent fast tick must NOT re-fire (force-eval popped + shared
        #    dedup cooldown + single price sample = no move_fires surface).
        await sc._fast_watch_tick(cfg, dedup)

    _run(_drive())

    assert len(buy_calls) == 1, (
        f"a token armed once must fire AT MOST once (got {len(buy_calls)} buys)"
    )
    assert ADDR not in sc._fast_force_eval, (
        "the completed instant fire must clear the force-eval flag"
    )
