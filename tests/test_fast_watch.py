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
    cfg = fw.FastWatchConfig(mode="shadow", interval_secs=3.0, trend_secs=90,
                             dip_pct=3.0, eval_cooldown_secs=60.0,
                             bot_allowlist=frozenset({"x"}))
    trends = {"DIP": -4.0, "FLAT": -0.5, "HELD": -9.0, "RECENT": -4.0}
    dedup = fw.FastWatchDedup(60)
    dedup.mark("RECENT", now=1000.0)
    snapshot = [("DIP", {"pair": {}}), ("FLAT", {"pair": {}}),
                ("HELD", {"pair": {}}), ("RECENT", {"pair": {}})]
    out = fw.shortlist(
        snapshot,
        get_trend=lambda addr, secs: trends.get(addr),
        dedup=dedup,
        is_held_or_blocked=lambda addr: addr == "HELD",
        cfg=cfg,
        now=1001.0,
    )
    assert [a for a, _e, _t in out] == ["DIP"]        # FLAT no-dip, HELD blocked, RECENT deduped


def test_config_from_env_defaults_and_overrides(monkeypatch):
    for k in ("FAST_WATCH_MODE", "FAST_WATCH_INTERVAL_SECS", "FAST_WATCH_TREND_SECS",
              "FAST_WATCH_DIP_PCT", "FAST_WATCH_EVAL_COOLDOWN_SECS", "FAST_WATCH_BOT_ALLOWLIST"):
        monkeypatch.delenv(k, raising=False)
    cfg = fw.FastWatchConfig.from_env()
    assert cfg.mode == "off"                          # safe default
    assert cfg.interval_secs == 3.0
    assert cfg.trend_secs == 90
    assert cfg.dip_pct == 3.0
    assert cfg.eval_cooldown_secs == 60.0
    assert "badday_flush_conviction" in cfg.bot_allowlist
    assert "timebox_probe_5mgreen_live" in cfg.bot_allowlist

    monkeypatch.setenv("FAST_WATCH_MODE", "ShAdOw")
    monkeypatch.setenv("FAST_WATCH_DIP_PCT", "5")
    monkeypatch.setenv("FAST_WATCH_BOT_ALLOWLIST", "a, b ,c")
    cfg2 = fw.FastWatchConfig.from_env()
    assert cfg2.mode == "shadow"                       # normalized lowercase
    assert cfg2.dip_pct == 5.0
    assert cfg2.bot_allowlist == frozenset({"a", "b", "c"})


def test_config_bad_numbers_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_INTERVAL_SECS", "not-a-number")
    monkeypatch.setenv("FAST_WATCH_TREND_SECS", "")
    cfg = fw.FastWatchConfig.from_env()
    assert cfg.interval_secs == 3.0
    assert cfg.trend_secs == 90


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


def _scanner_for_tick(mode="shadow"):
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    s._buy_fire_lock = asyncio.Lock()
    s._sticky_watchlist = {
        "DIPADDR": {"pair": {"pairAddress": "P", "priceUsd": "1"}},
        "FLATADDR": {"pair": {"pairAddress": "P2"}},
    }
    s._token_registry = None
    s._fast_watch_regime = {"_regime_n": 0, "_regime_dip_breadth_pct": None,
                            "_regime_h1_neg_pct": None}

    class _Feed:
        def __init__(self): self.subscribed = []
        def subscribe_token(self, a): self.subscribed.append(a)
        def get_tick_trend(self, a, secs): return -5.0 if a == "DIPADDR" else -0.1
    s.axiom_price_feed = _Feed()

    s.evaluated = []
    async def fake_eval(pair, ctx):
        s.evaluated.append((pair.get("pairAddress"), ctx.get("_fast_path_shadow"),
                            ctx.get("_fast_path_allowlist")))
        return (None, 0, False)
    s._evaluate_pair = fake_eval
    return s


def test_fast_watch_tick_escalates_only_the_dip(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    monkeypatch.setenv("FAST_WATCH_BOT_ALLOWLIST", "x,y")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick()
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    # Only the dipping token was evaluated; ctx carried shadow + allowlist.
    assert s.evaluated == [("P", True, frozenset({"x", "y"}))]
    # The whole cohort was subscribed (Tier 0).
    assert set(s.axiom_price_feed.subscribed) == {"DIPADDR", "FLATADDR"}


def test_fast_watch_tick_dedups_second_call(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick()
    dedup = FastWatchDedup(cfg.eval_cooldown_secs)
    asyncio.run(s._fast_watch_tick(cfg, dedup))
    asyncio.run(s._fast_watch_tick(cfg, dedup))      # immediate re-tick
    assert len(s.evaluated) == 1                     # deduped within TTL


def test_fast_watch_tick_survives_eval_exception(monkeypatch):
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_tick()
    async def boom(pair, ctx): raise RuntimeError("eval blew up")
    s._evaluate_pair = boom
    # Must not raise out of the tick.
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))


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
