# tests/test_fast_watch.py
import os
import importlib
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
