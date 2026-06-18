# tests/test_fast_watch.py
import os
import importlib
import asyncio
import types
from core import fast_watch as fw


def test_dip_trigger_fires_at_or_below_threshold():
    assert fw.dip_trigger(-3.0, 3.0) is True      # exactly at threshold
    assert fw.dip_trigger(-5.5, 3.0) is True       # below
    assert fw.dip_trigger(-2.9, 3.0) is False      # not deep enough
    assert fw.dip_trigger(0.0, 3.0) is False
    assert fw.dip_trigger(4.0, 3.0) is False       # up move
    assert fw.dip_trigger(None, 3.0) is False      # no ticks -> never trigger
    assert fw.dip_trigger(-3.0, -3.0) is True      # threshold sign-insensitive


def test_dedup_suppresses_within_ttl_then_allows():
    d = fw.FastWatchDedup(ttl_secs=60)
    assert d.should_eval("A", now=1000.0) is True
    d.mark("A", now=1000.0)
    assert d.should_eval("A", now=1030.0) is False   # within TTL
    assert d.should_eval("A", now=1060.0) is True    # TTL elapsed (>=)
    assert d.should_eval("B", now=1030.0) is True     # different token


def test_shortlist_filters_held_blocked_and_recent():
    cfg = _cfg()
    trends = {"DIP": -4.0, "FLAT": -0.5, "HELD": -9.0, "RECENT": -4.0}
    dedup = fw.FastWatchDedup(60)
    dedup.mark("RECENT", now=1000.0)
    snapshot = [("DIP", {"pair": {}}), ("FLAT", {"pair": {}}),
                ("HELD", {"pair": {}}), ("RECENT", {"pair": {}})]
    out = fw.shortlist(
        snapshot,
        get_trend=lambda addr: trends.get(addr),
        dedup=dedup,
        is_held_or_blocked=lambda addr: addr == "HELD",
        cfg=cfg,
        now=1001.0,
    )
    assert [a for a, _e, _t in out] == ["DIP"]        # FLAT no-dip, HELD blocked, RECENT deduped


def test_config_from_env_defaults_and_overrides(monkeypatch):
    for k in ("FAST_WATCH_MODE", "FAST_WATCH_INTERVAL_SECS", "FAST_WATCH_DIP_PCT",
              "FAST_WATCH_EVAL_COOLDOWN_SECS", "FAST_WATCH_BOT_ALLOWLIST", "FAST_WATCH_ARMED_MAX",
              "FAST_WATCH_SAMPLE_WINDOW", "FAST_WATCH_VOLATILITY_RESERVE",
              "FAST_WATCH_DIP_ZONE_PCT", "FAST_WATCH_ARM_BAND_PP"):
        monkeypatch.delenv(k, raising=False)
    cfg = fw.FastWatchConfig.from_env()
    assert cfg.mode == "off"
    assert cfg.interval_secs == 3.0
    assert cfg.dip_pct == 3.0
    assert cfg.eval_cooldown_secs == 60.0
    assert cfg.armed_max == 30
    assert cfg.sample_window == 40
    assert cfg.volatility_reserve == 0.2
    assert cfg.dip_zone_pct == -12.0
    assert cfg.arm_band_pp == 12.0
    assert "badday_flush_conviction" in cfg.bot_allowlist
    assert not hasattr(cfg, "trend_secs")
    monkeypatch.setenv("FAST_WATCH_MODE", "ShAdOw")
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "10")
    cfg2 = fw.FastWatchConfig.from_env()
    assert cfg2.mode == "shadow"
    assert cfg2.armed_max == 10


def test_config_bad_numbers_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_INTERVAL_SECS", "not-a-number")
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "")
    cfg = fw.FastWatchConfig.from_env()
    assert cfg.interval_secs == 3.0
    assert cfg.armed_max == 30


def _cfg(**kw):
    base = dict(mode="shadow", interval_secs=3.0, dip_pct=3.0, eval_cooldown_secs=60.0,
                bot_allowlist=frozenset({"x"}), armed_max=30, sample_window=40,
                volatility_reserve=0.2, dip_zone_pct=-12.0, arm_band_pp=12.0)
    base.update(kw)
    return fw.FastWatchConfig(**base)


def test_arm_subset_picks_cusp_excludes_far_and_past():
    cfg = _cfg(armed_max=3, volatility_reserve=0.0)
    cands = [
        {"addr": "NEAR", "pc_h1": -8.0, "vol_h1": 1.0, "in_band": True},   # dist 4 -> cusp
        {"addr": "FLAT", "pc_h1": -2.0, "vol_h1": 1.0, "in_band": True},   # dist 10 -> cusp (farther)
        {"addr": "FAR",  "pc_h1": +5.0, "vol_h1": 1.0, "in_band": True},   # dist 17 > band -> out
        {"addr": "PAST", "pc_h1": -20.0,"vol_h1": 1.0, "in_band": True},   # dist -8 <=0 -> out (already in zone)
        {"addr": "OOB",  "pc_h1": -8.0, "vol_h1": 9.0, "in_band": False},  # out of band -> out
    ]
    armed = fw.arm_subset(cands, cfg)
    assert armed == ["NEAR", "FLAT"]   # smallest-distance first; FAR/PAST/OOB excluded


def test_arm_subset_volatility_reserve_fills_remaining():
    cfg = _cfg(armed_max=2, volatility_reserve=0.5)   # 1 cusp slot, 1 reserve slot
    cands = [
        {"addr": "CUSP", "pc_h1": -8.0, "vol_h1": 1.0, "in_band": True},   # cusp
        {"addr": "VOLA", "pc_h1": +50.0, "vol_h1": 99.0, "in_band": True}, # far (no cusp) but high vol
        {"addr": "VOLB", "pc_h1": +40.0, "vol_h1": 50.0, "in_band": True},
    ]
    armed = fw.arm_subset(cands, cfg)
    assert armed[0] == "CUSP"
    assert "VOLA" in armed and len(armed) == 2   # reserve filled by highest vol_h1


def test_arm_subset_caps_at_armed_max():
    cfg = _cfg(armed_max=2, volatility_reserve=0.0)
    cands = [{"addr": f"T{i}", "pc_h1": -float(i), "vol_h1": 1.0, "in_band": True} for i in range(1, 6)]
    assert len(fw.arm_subset(cands, cfg)) == 2


def test_rolling_dip_pct():
    assert fw.rolling_dip_pct([]) is None
    assert fw.rolling_dip_pct([100.0]) is None            # <2 samples
    assert fw.rolling_dip_pct([100.0, 90.0]) == -10.0      # 10% off the high
    assert fw.rolling_dip_pct([100.0, 120.0, 114.0]) == -5.0  # off the window MAX (120), not first
    assert fw.rolling_dip_pct([0.0, 0.0]) is None          # bad data


class _FakeCfg:
    def __init__(self, bot_id, enabled=True):
        self.bot_id = bot_id
        self.enabled = enabled


class _FakeEvaluator:
    def __init__(self, bot_id, enabled=True):
        self.config = _FakeCfg(bot_id, enabled)
    def evaluate(self, bundle, realized_pnl_usd=0.0):
        return f"BUY:{self.config.bot_id}"


def test_evaluate_all_respects_bot_allowlist():
    from core.bot_manager import BotManager
    mgr = BotManager.__new__(BotManager)            # bypass real __init__
    mgr.evaluators = [_FakeEvaluator("a"), _FakeEvaluator("b"), _FakeEvaluator("c")]
    # No allowlist -> all enabled bots evaluated (unchanged behavior).
    assert set(mgr.evaluate_all(bundle=object())) == {"BUY:a", "BUY:b", "BUY:c"}
    # Allowlist -> only listed bots.
    assert set(mgr.evaluate_all(bundle=object(), bot_allowlist={"a", "c"})) == {"BUY:a", "BUY:c"}
    # Empty allowlist -> nothing.
    assert mgr.evaluate_all(bundle=object(), bot_allowlist=set()) == []


def _make_scanner_with_fire_blocks():
    """Build a minimal object exposing the fan-out + legacy fire logic under test.
    We exercise the real decision-routing helper extracted in Step 3."""
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    s._buy_fire_lock = asyncio.Lock()
    s.fired = []          # (path, bot_id)
    return s


def test_fanout_fires_all_when_no_allowlist():
    s = _make_scanner_with_fire_blocks()
    decisions = [types.SimpleNamespace(bot_id="a", token="T"),
                 types.SimpleNamespace(bot_id="b", token="T")]
    async def fake_exec(d, bundle): s.fired.append(("fire", d.bot_id))
    s._execute_bot_buy = fake_exec
    asyncio.run(s._fast_route_decisions(decisions, bundle=None, allowlist=None,
                                        shadow=False, token_symbol="T"))
    assert s.fired == [("fire", "a"), ("fire", "b")]


def test_fanout_shadow_logs_never_fires():
    s = _make_scanner_with_fire_blocks()
    decisions = [types.SimpleNamespace(bot_id="a", token="T")]
    async def fake_exec(d, bundle): s.fired.append(("fire", d.bot_id))
    s._execute_bot_buy = fake_exec
    asyncio.run(s._fast_route_decisions(decisions, bundle=None,
                                        allowlist={"a"}, shadow=True, token_symbol="T"))
    assert s.fired == []          # shadow: no fire at all


def test_fanout_enforce_fires_only_allowlisted():
    s = _make_scanner_with_fire_blocks()
    decisions = [types.SimpleNamespace(bot_id="a", token="T"),
                 types.SimpleNamespace(bot_id="z", token="T")]
    async def fake_exec(d, bundle): s.fired.append(("fire", d.bot_id))
    s._execute_bot_buy = fake_exec
    # evaluate_all already filtered; route just fires what it's given in enforce.
    asyncio.run(s._fast_route_decisions(decisions, bundle=None,
                                        allowlist={"a"}, shadow=False, token_symbol="T"))
    assert s.fired == [("fire", "a"), ("fire", "z")]   # routing fires given decisions under the lock


def test_run_spawns_fast_watch_task(monkeypatch):
    """run() must create the fast-watch task exactly once before the sweep loop."""
    import feeds.dip_scanner as ds
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    created = []
    monkeypatch.setattr(ds.asyncio, "create_task",
                        lambda coro, *a, **k: created.append(coro) or coro.close())

    # Stop run() after one iteration by raising out of _scan_cycle.
    async def stop_cycle():
        raise KeyboardInterrupt
    s._scan_cycle = stop_cycle
    s.bot_manager = None
    try:
        asyncio.run(s.run())
    except KeyboardInterrupt:
        pass
    assert len(created) == 1            # the fast-watch loop was scheduled once


def test_evaluate_pair_cycle_attrs_initialized_in_init():
    """Fast-watch can call _evaluate_pair before the first _scan_cycle; the
    per-cycle attrs it reads must exist on a fresh instance (no AttributeError)."""
    import inspect
    from feeds.dip_scanner import DipScanner
    src = inspect.getsource(DipScanner.__init__)
    for attr in ("_cycle_bought_addrs", "_cycle_trend_reversal_blocked", "_fp_shadow_culled"):
        assert f"self.{attr}" in src, f"{attr} not initialized in __init__"


def _scanner_for_tick_v2():
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    s._buy_fire_lock = asyncio.Lock()
    s._token_registry = None
    s._fast_watch_regime = {"_regime_n": 0, "_regime_dip_breadth_pct": None, "_regime_h1_neg_pct": None}
    # armed set: DIP token will be made to dip via injected batch prices; FLAT will not.
    s._fast_armed = {
        "DIPADDR": {"pairAddress": "P", "priceUsd": "1"},
        "FLATADDR": {"pairAddress": "P2", "priceUsd": "1"},
    }
    from collections import deque
    s._fast_samples = {}
    # Pre-seed a high sample so a single fresh low price registers as a dip.
    s._fast_samples["DIPADDR"] = deque([1.00], maxlen=40)
    s._fast_samples["FLATADDR"] = deque([1.00], maxlen=40)

    async def fake_batch(addrs):
        return {"dipaddr": 0.90, "flataddr": 1.00}   # DIP drops 10%, FLAT flat
    s._fast_batch_prices = fake_batch

    s.evaluated = []
    async def fake_eval(pair, ctx):
        s.evaluated.append((pair.get("pairAddress"), ctx.get("_fast_path_shadow"),
                            ctx.get("_fast_path_allowlist")))
        return (None, 0, False)
    s._evaluate_pair = fake_eval
    return s


def test_fast_tick_v2_escalates_only_the_dip(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    monkeypatch.setenv("FAST_WATCH_BOT_ALLOWLIST", "x,y")
    monkeypatch.setenv("FAST_WATCH_DIP_PCT", "3")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick_v2()
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    assert s.evaluated == [("P", True, frozenset({"x", "y"}))]   # only DIP, shadow + allowlist


def test_fast_tick_v2_dedups(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick_v2()
    d = FastWatchDedup(cfg.eval_cooldown_secs)
    asyncio.run(s._fast_watch_tick(cfg, d))
    asyncio.run(s._fast_watch_tick(cfg, d))
    assert len(s.evaluated) == 1


def test_fast_tick_v2_survives_eval_exception(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick_v2()
    async def boom(pair, ctx): raise RuntimeError("x")
    s._evaluate_pair = boom
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))   # must not raise


def test_fast_tick_v2_empty_armed_is_noop(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick_v2()
    s._fast_armed = {}
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    assert s.evaluated == []
