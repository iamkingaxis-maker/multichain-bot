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
    trends = {"DIP": -4.0, "FLAT": -0.5, "HELD": -9.0, "RECENT": -4.0}
    dedup = fw.FastWatchDedup(60)
    dedup.mark("RECENT", now=1000.0)
    snapshot = [("DIP", {"pair": {}}), ("FLAT", {"pair": {}}),
                ("HELD", {"pair": {}}), ("RECENT", {"pair": {}})]
    out = fw.shortlist(
        snapshot,
        trigger_fn=lambda a: trends.get(a, 0) <= -3,
        dedup=dedup,
        is_held_or_blocked=lambda a: a == "HELD",
        now=1001.0,
    )
    assert [a for a, _e in out] == ["DIP"]        # FLAT no-dip, HELD blocked, RECENT deduped


def test_config_from_env_defaults_and_overrides(monkeypatch):
    for k in ("FAST_WATCH_MODE", "FAST_WATCH_INTERVAL_SECS", "FAST_WATCH_DIP_PCT",
              "FAST_WATCH_RISE_PCT",
              "FAST_WATCH_EVAL_COOLDOWN_SECS", "FAST_WATCH_BOT_ALLOWLIST", "FAST_WATCH_ARMED_MAX",
              "FAST_WATCH_SAMPLE_WINDOW", "FAST_WATCH_VOLATILITY_RESERVE",
              "FAST_WATCH_DIP_ZONE_PCT", "FAST_WATCH_ARM_BAND_PP"):
        monkeypatch.delenv(k, raising=False)
    cfg = fw.FastWatchConfig.from_env()
    assert cfg.mode == "off"
    assert cfg.interval_secs == 3.0
    assert cfg.dip_pct == 3.0
    assert cfg.rise_pct == 3.0
    assert cfg.eval_cooldown_secs == 60.0
    assert cfg.armed_max == 500
    assert cfg.sample_window == 40
    assert cfg.arm_band_pp == 15.0
    assert cfg.hot_max == 50
    assert cfg.full_poll_every == 3
    assert not hasattr(cfg, "dip_zone_pct")
    assert not hasattr(cfg, "volatility_reserve")
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
    assert cfg.armed_max == 500


def _cfg(**kw):
    base = dict(mode="shadow", interval_secs=3.0, dip_pct=3.0, rise_pct=3.0,
                eval_cooldown_secs=60.0, bot_allowlist=frozenset({"x"}), armed_max=30,
                sample_window=40, arm_band_pp=15.0, hot_max=50, full_poll_every=3)
    base.update(kw)
    return fw.FastWatchConfig(**base)


def test_arm_subset_arms_dips_and_pumps_volume_ranked():
    # pc_h1-AGNOSTIC: arm EVERY in_band token regardless of direction (dips AND
    # pumps) — the fleet buys both. Rank by vol_h1 desc. pc_h1 no longer gates.
    cfg = _cfg(armed_max=5, arm_band_pp=15.0)
    cands = [
        {"addr": "DEEPDIP", "pc_h1": -36.0, "vol_h1": 1.0, "in_band": True},  # deep dip -> ARM
        {"addr": "BIGPUMP", "pc_h1": +905.0, "vol_h1": 99.0, "in_band": True}, # huge pump -> ARM (was excluded)
        {"addr": "MID",     "pc_h1": -2.0, "vol_h1": 50.0, "in_band": True},  # near-flat -> ARM
    ]
    armed = fw.arm_subset(cands, cfg)
    assert armed == ["BIGPUMP", "MID", "DEEPDIP"]  # all in_band, ranked vol desc; pump now armed


def test_arm_subset_arms_pumps_and_none_pc_h1():
    # pc_h1-AGNOSTIC: pumped tokens (momentum bots buy these) AND pc_h1=None
    # tokens are NOW armed. Only the out-of-band token is excluded.
    cfg = _cfg(armed_max=5, arm_band_pp=15.0)
    cands = [
        {"addr": "INPLAY",  "pc_h1": -8.0, "vol_h1": 1.0, "in_band": True},   # down -> keep
        {"addr": "DEEPDIP", "pc_h1": -36.0, "vol_h1": 5.0, "in_band": True},  # deep dip -> keep
        {"addr": "DIP25",   "pc_h1": -24.8, "vol_h1": 4.0, "in_band": True},  # real fleet buy -> keep
        {"addr": "BIGPUMP", "pc_h1": +905.0, "vol_h1": 9.0, "in_band": True}, # momentum buy -> KEEP now
        {"addr": "OOB",     "pc_h1": -5.0, "vol_h1": 99.0, "in_band": False}, # out of band -> drop
        {"addr": "NOPC",    "pc_h1": None, "vol_h1": 7.0, "in_band": True},   # no pc_h1 -> KEEP now
    ]
    armed = fw.arm_subset(cands, cfg)
    # ranked by vol desc among in_band kept: BIGPUMP(9), NOPC(7), DEEPDIP(5), DIP25(4), INPLAY(1)
    assert armed == ["BIGPUMP", "NOPC", "DEEPDIP", "DIP25", "INPLAY"]
    assert "BIGPUMP" in armed       # pump armed (momentum bots were 0/38)
    assert "NOPC" in armed          # pc_h1=None armed (was dropped)
    assert "DEEPDIP" in armed       # dip still armed
    assert "OOB" not in armed       # only out-of-band excluded


def test_arm_subset_caps_at_armed_max():
    cfg = _cfg(armed_max=2, arm_band_pp=15.0)
    # mix of dips and pumps; cap still applies after vol ranking
    cands = [{"addr": f"T{i}", "pc_h1": (-30.0 if i % 2 else +50.0), "vol_h1": float(i),
              "in_band": True} for i in range(1, 6)]
    armed = fw.arm_subset(cands, cfg)
    assert len(armed) == 2
    assert armed == ["T5", "T4"]   # highest vol first (dips+pumps), capped at armed_max


def test_hot_subset_top_n_by_volume():
    """TIERED POLL: hot subset = top-N armed addrs ranked by pair volume.h1 desc."""
    armed = {
        "LOW":  {"volume": {"h1": 1.0}},
        "HIGH": {"volume": {"h1": 99.0}},
        "MID":  {"volume": {"h1": 50.0}},
        " NONE": {"volume": {}},          # missing h1 -> treated as 0
    }
    assert fw.hot_subset(armed, hot_max=2) == ["HIGH", "MID"]   # top-2 by vol desc
    # cap respected: hot_max larger than the set returns all, still ranked
    assert fw.hot_subset(armed, hot_max=10) == ["HIGH", "MID", "LOW", " NONE"]
    # original case preserved (FIX5: never lowercase the Jupiter query addr)
    armed2 = {"AbCdEf": {"volume": {"h1": 5.0}}}
    assert fw.hot_subset(armed2, hot_max=5) == ["AbCdEf"]


def test_hot_subset_cap_zero_or_empty():
    assert fw.hot_subset({"A": {"volume": {"h1": 1.0}}}, hot_max=0) == []
    assert fw.hot_subset({}, hot_max=50) == []
    assert fw.hot_subset(None, hot_max=50) == []


def test_rolling_dip_pct():
    assert fw.rolling_dip_pct([]) is None
    assert fw.rolling_dip_pct([100.0]) is None            # <2 samples
    assert fw.rolling_dip_pct([100.0, 90.0]) == -10.0      # 10% off the high
    assert fw.rolling_dip_pct([100.0, 120.0, 114.0]) == -5.0  # off the window MAX (120), not first
    assert fw.rolling_dip_pct([0.0, 0.0]) is None          # bad data


def test_rolling_rise_pct():
    assert fw.rolling_rise_pct([]) is None
    assert fw.rolling_rise_pct([100.0]) is None            # <2 samples
    assert fw.rolling_rise_pct([100.0, 110.0]) == 10.0     # 10% off the low
    assert fw.rolling_rise_pct([100.0, 80.0, 84.0]) == 5.0  # off the window MIN (80), not first
    assert fw.rolling_rise_pct([0.0, 0.0]) is None         # bad data


def test_move_fires_either_direction():
    assert fw.move_fires([100.0, 96.0], 3.0, 3.0) is True    # dip -4% fires
    assert fw.move_fires([100.0, 104.0], 3.0, 3.0) is True   # rise +4% fires
    assert fw.move_fires([100.0, 101.0], 3.0, 3.0) is False  # neither (within bands)
    assert fw.move_fires([100.0], 3.0, 3.0) is False         # <2 samples -> False


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
    """run() must create the fast-watch task before the sweep loop. It may also
    spawn other background tasks (log-rotator, on-chain feed) depending on env;
    assert the fast-watch coro is AMONG the created tasks rather than an exact
    count (which is brittle to those flag-gated spawns)."""
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
    assert len(created) >= 1            # at least one background task scheduled
    # the fast-watch loop is among the scheduled coroutines
    names = [getattr(c, "__qualname__", "") or getattr(getattr(c, "cr_code", None),
             "co_qualname", "") for c in created]
    assert any("_fast_watch_loop" in n for n in names)


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
        "DIPADDR": {"pairAddress": "P", "priceUsd": "1", "volume": {"h1": 9.0}},
        "FLATADDR": {"pairAddress": "P2", "priceUsd": "1", "volume": {"h1": 1.0}},
    }
    s._fw_tick_n = 0
    s._fw_stats = {"armed_hits": 0, "armed_misses": 0, "by_bot": {},
                   "last_tick": {}, "ticks": 0, "would_fire": 0}
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


def _scanner_for_tier(armed):
    """Scanner wired so _fast_batch_prices RECORDS the addr list it is asked for
    (one record per tick) and returns a flat price for each, so we can assert
    which tier (hot-only vs full) was polled on a given tick."""
    from feeds.dip_scanner import DipScanner
    from collections import deque
    s = DipScanner.__new__(DipScanner)
    s._buy_fire_lock = asyncio.Lock()
    s._token_registry = None
    s._fast_watch_regime = {"_regime_n": 0, "_regime_dip_breadth_pct": None,
                            "_regime_h1_neg_pct": None}
    s._fast_armed = armed
    s._fast_samples = {a: deque([1.00], maxlen=40) for a in armed}
    s._fast_samples_ts = {}
    s._fw_tick_n = 0
    s._fw_stats = {"armed_hits": 0, "armed_misses": 0, "by_bot": {},
                   "last_tick": {}, "ticks": 0, "would_fire": 0}
    s.fetched = []     # list[list[addr]] — one entry per _fast_batch_prices call

    async def fake_batch(addrs):
        s.fetched.append(list(addrs))
        return {a.lower(): 1.00 for a in addrs}
    s._fast_batch_prices = fake_batch

    s.evaluated = []
    async def fake_eval(pair, ctx):
        s.evaluated.append(pair.get("pairAddress"))
        return (None, 0, False)
    s._evaluate_pair = fake_eval
    return s


def _tier_cfg(**kw):
    base = dict(mode="shadow", interval_secs=3.0, dip_pct=3.0, rise_pct=3.0,
                eval_cooldown_secs=60.0, bot_allowlist=frozenset({"x"}),
                armed_max=500, sample_window=40, arm_band_pp=15.0,
                hot_max=2, full_poll_every=3)
    base.update(kw)
    return fw.FastWatchConfig(**base)


def _tier_armed(n):
    # n armed tokens, descending volume so hot subset is deterministic (T0 highest)
    return {f"T{i}": {"pairAddress": f"P{i}", "priceUsd": "1",
                      "volume": {"h1": float(n - i)}} for i in range(n)}


def test_tiered_non_full_tick_polls_hot_only():
    """On a NON-full-poll tick (tick_n % full_poll_every != 0) only the hot subset
    addrs are passed to _fast_batch_prices."""
    from core.fast_watch import FastWatchDedup
    s = _scanner_for_tier(_tier_armed(10))
    cfg = _tier_cfg(hot_max=2, full_poll_every=3)
    s._fw_tick_n = 0                      # next tick -> tick_n becomes 1 (1%3 != 0 -> hot only)
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(60)))
    assert len(s.fetched) == 1
    assert s.fetched[0] == ["T0", "T1"]    # hot subset only (top-2 by volume)


def test_tiered_full_tick_polls_full_armed_set():
    """On a full-poll tick (tick_n % full_poll_every == 0) the FULL armed set is
    fetched (hot + the remaining tokens), covering 100% of the universe."""
    from core.fast_watch import FastWatchDedup
    s = _scanner_for_tier(_tier_armed(10))
    cfg = _tier_cfg(hot_max=2, full_poll_every=3)
    s._fw_tick_n = 2                      # next tick -> 3 (3%3 == 0 -> full poll)
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(60)))
    # full tick fetches the full armed set (order: hot first, then remainder)
    polled = sorted(a for call in s.fetched for a in call)
    assert polled == sorted(f"T{i}" for i in range(10))   # 100% coverage


def test_tiered_hot_token_dip_escalates_on_hot_only_tick():
    """A HOT token that dips escalates even on a hot-only tick; samples updated."""
    from collections import deque
    from core.fast_watch import FastWatchDedup
    s = _scanner_for_tier(_tier_armed(10))
    s._fast_samples["T0"] = deque([1.00], maxlen=40)   # pre-seed a high sample
    async def dip_batch(addrs):
        s.fetched.append(list(addrs))
        return {a.lower(): (0.90 if a == "T0" else 1.00) for a in addrs}   # T0 dips 10%
    s._fast_batch_prices = dip_batch
    async def pinned_eval(pair, ctx):
        s.evaluated.append(pair.get("pairAddress"))
        return (None, 0, False)
    s._evaluate_pair = pinned_eval
    # avoid the pinned-price network hop
    class _T:
        async def _get_token_price(self, a, pair_address=""):
            return None
    s.trader = _T()
    cfg = _tier_cfg(hot_max=2, full_poll_every=3)
    s._fw_tick_n = 0                      # tick -> 1 -> hot only
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(60)))
    assert s.fetched[0] == ["T0", "T1"]   # hot only
    assert s.evaluated == ["P0"]          # the dipping HOT token escalated
    assert list(s._fast_samples["T0"])[-1] == 0.90   # sample updated for polled token


def test_tiered_tick_survives_fetch_exception():
    """A fetch error on a tick doesn't crash the tick."""
    from core.fast_watch import FastWatchDedup
    s = _scanner_for_tier(_tier_armed(10))
    async def boom(addrs):
        raise RuntimeError("fetch down")
    s._fast_batch_prices = boom
    cfg = _tier_cfg(hot_max=2, full_poll_every=3)
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(60)))   # must not raise


def test_tiered_stats_record_both_tiers():
    """_fw_stats.last_tick records armed/polled/fired; full=0 on a hot-only tick
    and the full count on a full tick."""
    from core.fast_watch import FastWatchDedup
    s = _scanner_for_tier(_tier_armed(10))
    cfg = _tier_cfg(hot_max=2, full_poll_every=3)
    # hot-only tick (tick 1)
    s._fw_tick_n = 0
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(60)))
    lt = s._fw_stats["last_tick"]
    assert lt["hot"] == 2 and lt["full"] == 0 and lt["armed"] == 10
    # full tick (tick 3)
    s._fw_tick_n = 2
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(60)))
    lt = s._fw_stats["last_tick"]
    assert lt["full"] == 10 and lt["armed"] == 10


def test_fast_batch_prices_uses_jupiter_when_primary(monkeypatch):
    """JUPITER_PRICE_PRIMARY=on -> _fast_batch_prices builds lite-api.jup.ag
    price/v3 URLs chunked at 50, SERIALIZED, parses usdPrice -> {addr_lower: price}."""
    monkeypatch.setenv("JUPITER_PRICE_PRIMARY", "on")
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    addrs = [f"M{i}" for i in range(120)]
    captured = {"urls": []}

    class _FakeResp:
        def __init__(self, url):
            self._url = url
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        @property
        def status(self):
            return 200
        async def json(self, content_type=None):
            # echo back a Jupiter payload for the ids in this chunk
            ids = self._url.split("ids=", 1)[1].split(",")
            return {mid: {"usdPrice": 0.5, "blockId": 1} for mid in ids}

    class _FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def get(self, url, timeout=None):
            captured["urls"].append(url)
            return _FakeResp(url)

    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession())

    out = asyncio.run(s._fast_batch_prices(addrs))
    # 120 ids -> chunked at 50 -> 3 calls
    assert len(captured["urls"]) == 3
    assert all(u.startswith("https://lite-api.jup.ag/price/v3?ids=") for u in captured["urls"])
    # chunk sizes 50/50/20 (count commas+1)
    sizes = [u.split("ids=", 1)[1].count(",") + 1 for u in captured["urls"]]
    assert sizes == [50, 50, 20]
    # parsed -> lowercased addr -> price
    assert out == {f"m{i}": 0.5 for i in range(120)}


def test_fast_batch_prices_uses_single_session_for_whole_batch(monkeypatch):
    """PERF (2026-06-20): the Jupiter batch must open ONE shared aiohttp session
    for the whole batch (not one-per-chunk) and run the chunk GETs under a bounded
    asyncio.gather (FAST_WATCH_PRICE_CONCURRENCY)."""
    monkeypatch.setenv("JUPITER_PRICE_PRIMARY", "on")
    monkeypatch.setenv("FAST_WATCH_PRICE_CONCURRENCY", "4")
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    addrs = [f"M{i}" for i in range(120)]   # 3 chunks of 50/50/20
    sessions_created = {"n": 0}
    conc = {"running": 0, "peak": 0}

    class _FakeResp:
        def __init__(self, url):
            self._url = url
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        @property
        def status(self):
            return 200
        async def json(self, content_type=None):
            ids = self._url.split("ids=", 1)[1].split(",")
            return {mid: {"usdPrice": 0.5, "blockId": 1} for mid in ids}

    class _FakeReqCtx:
        def __init__(self, url):
            self._url = url
        async def __aenter__(self):
            conc["running"] += 1
            conc["peak"] = max(conc["peak"], conc["running"])
            await asyncio.sleep(0.01)
            return _FakeResp(self._url)
        async def __aexit__(self, *a):
            conc["running"] -= 1
            return False

    class _FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def get(self, url, timeout=None):
            return _FakeReqCtx(url)

    import aiohttp
    def _mk_session(*a, **k):
        sessions_created["n"] += 1
        return _FakeSession()
    monkeypatch.setattr(aiohttp, "ClientSession", _mk_session)

    out = asyncio.run(s._fast_batch_prices(addrs))
    assert sessions_created["n"] == 1            # ONE shared session, not per-chunk
    assert conc["peak"] > 1                       # chunks fetched concurrently
    assert conc["peak"] <= 4                       # bounded by concurrency
    assert out == {f"m{i}": 0.5 for i in range(120)}


def test_fast_batch_prices_uses_dexscreener_when_flag_off(monkeypatch):
    """Flag off -> existing DexScreener /latest/dex/tokens path (chunk 30), unchanged."""
    monkeypatch.delenv("JUPITER_PRICE_PRIMARY", raising=False)
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    addrs = ["AAA", "BBB"]
    captured = {"urls": []}

    class _FakeResp:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def json(self, content_type=None):
            return {"pairs": [{"baseToken": {"address": "AAA"}, "priceUsd": "1.5"},
                              {"baseToken": {"address": "BBB"}, "priceUsd": "2.0"}]}

    class _FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        def get(self, url, timeout=None):
            captured["urls"].append(url)
            return _FakeResp()

    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession())

    out = asyncio.run(s._fast_batch_prices(addrs))
    assert len(captured["urls"]) == 1
    assert captured["urls"][0].startswith("https://api.dexscreener.com/latest/dex/tokens/")
    assert out == {"aaa": 1.5, "bbb": 2.0}


def _scanner_for_arm(n_inband):
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    s.min_age_ms = 0
    s.min_mcap = 1.0
    s.max_mcap = 1e12
    s._fast_samples = {}
    now_ms = 10_000_000
    wl = {}
    for i in range(n_inband):
        wl[f"T{i}"] = {"pair": {
            "marketCap": 100_000,
            "liquidity": {"usd": 50_000},
            "pairCreatedAt": 1,
            "priceChange": {"h1": -1.0},   # |pc_h1| <= band -> in-play
            "volume": {"h1": float(i)},
        }}
    s._sticky_watchlist = wl
    return s, now_ms


def test_fast_arm_subset_arms_whole_watchlist_when_jupiter_primary(monkeypatch):
    """JUPITER_PRICE_PRIMARY=on -> arm ALL in-band candidates up to the rate-safe
    ceiling (no artificial small cap). 100 in-band < 500 ceiling -> all 100 armed."""
    monkeypatch.setenv("JUPITER_PRICE_PRIMARY", "on")
    monkeypatch.delenv("FAST_WATCH_ARMED_MAX", raising=False)
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()          # armed_max default 500 (rate-safe ceiling)
    s, now_ms = _scanner_for_arm(100)
    s._fast_arm_subset(cfg, now_ms)
    assert len(s._fast_armed) == 100   # whole in-band watchlist (under the 500 ceiling)


def test_fast_arm_subset_clamps_to_rate_safe_ceiling_under_jupiter(monkeypatch):
    """JUPITER_PRICE_PRIMARY=on lifts armed_max to n_inband, but clamps to the
    rate-safe FAST_WATCH_ARMED_MAX ceiling (default 500) so adding pumps to the
    armed set can't blow past the Jupiter ~110 req/min budget."""
    monkeypatch.setenv("JUPITER_PRICE_PRIMARY", "on")
    monkeypatch.delenv("FAST_WATCH_ARMED_MAX", raising=False)
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()          # armed_max default 500
    assert cfg.armed_max == 500
    s, now_ms = _scanner_for_arm(700)          # 700 in-band tokens
    s._fast_arm_subset(cfg, now_ms)
    assert len(s._fast_armed) == 500           # clamped to the rate-safe ceiling, not 700


def test_fast_arm_subset_caps_at_30_when_flag_off(monkeypatch):
    """Flag off -> existing armed_max=30 cap preserved (unchanged behavior)."""
    monkeypatch.delenv("JUPITER_PRICE_PRIMARY", raising=False)
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig(mode="shadow", interval_secs=3.0, dip_pct=3.0, rise_pct=3.0,
                          eval_cooldown_secs=60.0, bot_allowlist=frozenset({"x"}),
                          armed_max=30, sample_window=40, arm_band_pp=15.0,
                          hot_max=50, full_poll_every=3)
    s, now_ms = _scanner_for_arm(100)
    s._fast_arm_subset(cfg, now_ms)
    assert len(s._fast_armed) == 30    # capped at armed_max


def _scanner_for_pinned_tick(pinned_return):
    """Single-armed DIP scanner whose trader._get_token_price returns
    `pinned_return` (a float, None, or a callable(addr, pair_address)).
    The fake _evaluate_pair captures the escalated pair's priceUsd."""
    from feeds.dip_scanner import DipScanner
    from collections import deque
    s = DipScanner.__new__(DipScanner)
    s._buy_fire_lock = asyncio.Lock()
    s._token_registry = None
    s._fast_watch_regime = {"_regime_n": 0, "_regime_dip_breadth_pct": None,
                            "_regime_h1_neg_pct": None}
    # cached pair price = "1" (the stale pre-escalation value)
    s._fast_armed = {"DIPADDR": {"pairAddress": "POOLX", "priceUsd": "1",
                                 "volume": {"h1": 5.0}}}
    s._fast_samples = {"DIPADDR": deque([1.00], maxlen=40)}
    s._fw_tick_n = 0
    s._fw_stats = {"armed_hits": 0, "armed_misses": 0, "by_bot": {},
                   "last_tick": {}, "ticks": 0, "would_fire": 0}

    async def fake_batch(addrs):
        return {"dipaddr": 0.90}   # Jupiter aggregate fresh = 0.90 (10% dip)
    s._fast_batch_prices = fake_batch

    captured = {"calls": []}

    class _FakeTrader:
        async def _get_token_price(self, token_address, pair_address=""):
            captured["calls"].append((token_address, pair_address))
            if callable(pinned_return):
                return pinned_return(token_address, pair_address)
            return pinned_return
    s.trader = _FakeTrader()

    s.evaluated = []
    async def fake_eval(pair, ctx):
        s.evaluated.append(pair.get("priceUsd"))
        return (None, 0, False)
    s._evaluate_pair = fake_eval
    return s, captured


def test_fast_tick_injects_pair_pinned_price_not_jupiter(monkeypatch):
    """Escalated pair["priceUsd"] = the PAIR-PINNED price, not the Jupiter
    aggregate (0.90), and the pin is queried with the token's pairAddress."""
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    monkeypatch.setenv("FAST_WATCH_DIP_PCT", "3")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s, captured = _scanner_for_pinned_tick(0.123)   # pinned pool price
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    assert s.evaluated == ["0.123"]                  # pinned, NOT "0.9"
    assert captured["calls"] == [("DIPADDR", "POOLX")]   # pinned to the pool


def test_fast_tick_falls_back_to_jupiter_when_pin_none(monkeypatch):
    """Pinned fetch None -> use the Jupiter aggregate fresh price (0.90)."""
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    monkeypatch.setenv("FAST_WATCH_DIP_PCT", "3")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s, captured = _scanner_for_pinned_tick(None)
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    assert s.evaluated == ["0.9"]                    # Jupiter fresh fallback
    assert captured["calls"] == [("DIPADDR", "POOLX")]


def test_fast_tick_pin_zero_falls_back_to_jupiter(monkeypatch):
    """Pinned fetch <=0 -> use the Jupiter aggregate fresh price (0.90)."""
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    monkeypatch.setenv("FAST_WATCH_DIP_PCT", "3")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s, _captured = _scanner_for_pinned_tick(0.0)
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    assert s.evaluated == ["0.9"]                    # Jupiter fresh fallback


def test_fast_tick_leaves_cached_when_pin_and_jupiter_missing(monkeypatch):
    """Pin None AND no Jupiter fresh -> leave the cached pair price ("1")."""
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    monkeypatch.setenv("FAST_WATCH_DIP_PCT", "3")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s, _captured = _scanner_for_pinned_tick(None)
    # Pre-seed two high samples so move_fires triggers on the dip even though
    # the *current* batch returns no fresh price for the token.
    from collections import deque
    s._fast_samples["DIPADDR"] = deque([1.00, 0.90], maxlen=40)
    async def fake_batch(addrs):
        return {}   # no fresh price this tick
    s._fast_batch_prices = fake_batch
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    assert s.evaluated == ["1"]                      # cached pair price left untouched


def test_hitrate_log_marks_armed(monkeypatch, caplog):
    """FIX B: the hit-rate metric keys off the decision ADDRESS (mint), not the
    SYMBOL. A buy whose address is in _fast_armed must log armed=True (the prior
    symbol-vs-address compare was always-False -> the 0/36 phantom)."""
    import logging, types, asyncio as aio
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    s._buy_fire_lock = aio.Lock()
    s._fast_armed = {"TokenMintAddr111": {"pairAddress": "P"}}
    fired = []
    async def fake_exec(d, bundle): fired.append(d.bot_id)
    s._execute_bot_buy = fake_exec
    # token is the SYMBOL (does NOT match the armed key); address is the mint (matches).
    d = types.SimpleNamespace(bot_id="a", token="TOK", address="TokenMintAddr111")
    with caplog.at_level(logging.INFO):
        aio.run(s._fast_route_decisions([d], bundle=None, allowlist=None, shadow=False,
                                        token_symbol="TOK"))
    assert fired == ["a"]
    assert any("hit-rate" in r.message and "armed=True" in r.message for r in caplog.records)


def test_hitrate_log_armed_is_case_insensitive(monkeypatch, caplog):
    """FIX B: membership test lowercases both sides so a mixed-case decision
    address still matches an armed key in a different case."""
    import logging, types, asyncio as aio
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    s._buy_fire_lock = aio.Lock()
    s._fast_armed = {"AbCdEf123": {"pairAddress": "P"}}
    async def fake_exec(d, bundle): pass
    s._execute_bot_buy = fake_exec
    d = types.SimpleNamespace(bot_id="a", token="TOK", address="abcdef123")  # different case
    with caplog.at_level(logging.INFO):
        aio.run(s._fast_route_decisions([d], bundle=None, allowlist=None, shadow=False,
                                        token_symbol="TOK"))
    assert any("hit-rate" in r.message and "armed=True" in r.message for r in caplog.records)


def test_hitrate_log_not_armed_address(monkeypatch, caplog):
    """FIX B: an address that is NOT armed logs armed=False."""
    import logging, types, asyncio as aio
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    s._buy_fire_lock = aio.Lock()
    s._fast_armed = {"ArmedMint": {"pairAddress": "P"}}
    async def fake_exec(d, bundle): pass
    s._execute_bot_buy = fake_exec
    d = types.SimpleNamespace(bot_id="a", token="TOK", address="SomeOtherMint")
    with caplog.at_level(logging.INFO):
        aio.run(s._fast_route_decisions([d], bundle=None, allowlist=None, shadow=False,
                                        token_symbol="TOK"))
    assert any("hit-rate" in r.message and "armed=False" in r.message for r in caplog.records)


# ---- FIX 3: arm EVERY watched token (drop the mcap/liq gate entirely) --------
# in_band is now simply bool(pair) — any sticky entry with a non-empty cached
# pair dict is armed. The cached mcap/liq is UNRELIABLE for microcaps (missing
# liquidity.usd -> liq==0 -> the residual ~50% miss on tokens like Metacraft /
# BubbleMan), so ANY mcap/liq filter keeps dropping real buys. The bought tokens
# are all in the watched (sticky) set, so armed ⊇ bought is guaranteed.

def _scanner_for_lane_arm(pairs):
    """Scanner with min_mcap=$1M / max_mcap=$50M fleet band, watchlist = `pairs`
    {addr: pair_dict}. (min_mcap is retained on the instance but no longer
    consulted by _fast_arm_subset — the floor was dropped.)"""
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    s.min_age_ms = 0
    s.min_mcap = 1_000_000.0
    s.max_mcap = 50_000_000.0
    s._fast_samples = {}
    s._sticky_watchlist = {a: {"pair": p} for a, p in pairs.items()}
    return s, 10_000_000_000


def test_fast_arm_subset_arms_missing_liq_microcap(monkeypatch):
    """FIX 3: the Metacraft case — a $0.3M microcap whose cached pair has MISSING
    liquidity (no 'liquidity' key / liquidity.usd absent) was excluded by FIX 2's
    liq>0 gate and never armed (the residual ~50% miss). It is NOW armed because
    in_band = bool(pair). A >max_mcap token (BubbleMan +677%) is NOW armed too."""
    monkeypatch.setenv("BADDAY_LANE", "off")
    monkeypatch.delenv("YOUNG_TOKEN_PROBE", raising=False)
    monkeypatch.delenv("LOW_MCAP_PROBE", raising=False)
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "30")
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    now_ms = 10_000_000_000
    created = now_ms - int(24 * 3_600_000)
    pairs = {
        # Metacraft: $0.3M, cached pair has NO liquidity key (liq -> 0).
        "METACRAFT": {"marketCap": 300_000, "pairCreatedAt": created,
                      "priceChange": {"h1": -27.0}, "volume": {"h1": 5.0}},
        # Liquidity key present but usd absent (also -> liq 0).
        "BUBBLEMAN": {"marketCap": 300_000, "liquidity": {},
                      "pairCreatedAt": created, "priceChange": {"h1": +677.0},
                      "volume": {"h1": 9.0}},
        # A token well above max_mcap is NOW armed too (drop the ceiling).
        "HUGE100M": {"marketCap": 100_000_000, "liquidity": {"usd": 80_000},
                     "pairCreatedAt": created, "priceChange": {"h1": -5.0},
                     "volume": {"h1": 9.0}},
    }
    s, _ = _scanner_for_lane_arm(pairs)
    s._sticky_watchlist = {a: {"pair": p} for a, p in pairs.items()}
    s._fast_arm_subset(cfg, now_ms)
    armed = {k.lower() for k in s._fast_armed.keys()}   # FIX 4: armed keys are lowercased
    assert "metacraft" in armed     # missing-liq microcap -> NOW armed (FIX 3)
    assert "bubbleman" in armed     # missing-liq pump -> NOW armed (FIX 3)
    assert "huge100m" in armed      # mcap > max_mcap -> NOW armed (ceiling dropped)


def test_fast_arm_subset_arms_dip_pump_huge_and_deadliq_but_not_empty(monkeypatch):
    """FIX 3 + FIX 1 preserved: deep dip AND pump are armed; a >max_mcap token is
    NOW armed; a liq<=0 token is NOW armed; only a sticky entry with an EMPTY/None
    pair (in_band=bool(pair) false) is NOT armed."""
    monkeypatch.delenv("BADDAY_LANE", raising=False)
    monkeypatch.delenv("YOUNG_TOKEN_PROBE", raising=False)
    monkeypatch.delenv("LOW_MCAP_PROBE", raising=False)
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "30")
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    now_ms = 10_000_000_000
    created = now_ms - int(2 * 3_600_000)   # young (age gate dropped -> still armed)
    pairs = {
        "DEEPDIP": {"marketCap": 200_000, "liquidity": {"usd": 40_000},
                    "pairCreatedAt": created, "priceChange": {"h1": -36.0},
                    "volume": {"h1": 5.0}},
        "PUMP":    {"marketCap": 300_000, "liquidity": {"usd": 40_000},
                    "pairCreatedAt": created, "priceChange": {"h1": +905.0},
                    "volume": {"h1": 9.0}},
        "HUGE":    {"marketCap": 80_000_000, "liquidity": {"usd": 80_000},
                    "pairCreatedAt": created, "priceChange": {"h1": -5.0},
                    "volume": {"h1": 9.0}},
        "DEADLIQ": {"marketCap": 200_000, "liquidity": {"usd": 0.0},
                    "pairCreatedAt": created, "priceChange": {"h1": -30.0},
                    "volume": {"h1": 9.0}},
    }
    s, _ = _scanner_for_lane_arm(pairs)
    wl = {a: {"pair": p} for a, p in pairs.items()}
    wl["EMPTYPAIR"] = {"pair": {}}    # empty pair dict -> in_band bool({}) false
    wl["NONEPAIR"] = {"pair": None}   # None pair -> in_band false
    s._sticky_watchlist = wl
    s._fast_arm_subset(cfg, now_ms)
    armed = {k.lower() for k in s._fast_armed.keys()}   # FIX 4: armed keys are lowercased
    assert "deepdip" in armed        # dip armed (FIX 1)
    assert "pump" in armed           # pump armed (FIX 1)
    assert "huge" in armed           # mcap > max_mcap -> NOW armed (FIX 3)
    assert "deadliq" in armed        # liq <= 0 -> NOW armed (FIX 3)
    assert "emptypair" not in armed  # empty pair -> NOT armed
    assert "nonepair" not in armed   # None pair -> NOT armed


def test_fast_arm_subset_volume_ranks_under_cap(monkeypatch):
    """FIX 3: volume ranking + armed_max cap still hold — cap=2 -> top-2 by
    volume.h1, even with the whole watched set in_band."""
    monkeypatch.delenv("BADDAY_LANE", raising=False)
    monkeypatch.delenv("YOUNG_TOKEN_PROBE", raising=False)
    monkeypatch.delenv("LOW_MCAP_PROBE", raising=False)
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "2")
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    assert cfg.armed_max == 2
    now_ms = 10_000_000_000
    created = now_ms - int(24 * 3_600_000)
    pairs = {
        f"T{i}": {"marketCap": 300_000, "liquidity": {"usd": 40_000},
                  "pairCreatedAt": created, "priceChange": {"h1": -10.0},
                  "volume": {"h1": float(i)}}
        for i in range(1, 6)
    }
    s, _ = _scanner_for_lane_arm(pairs)
    s._sticky_watchlist = {a: {"pair": p} for a, p in pairs.items()}
    s._fast_arm_subset(cfg, now_ms)
    armed = {k.lower() for k in s._fast_armed.keys()}   # FIX 4: armed keys are lowercased
    assert armed == {"t4", "t5"}    # top-2 by volume.h1, capped at armed_max


# ---- FIX 4: arm from the EVALUATED UNIVERSE (pair_by_addr) ∪ sticky ----------
# The residual ~50% miss: tokens bought from NON-PERSISTED sources (gt_trending /
# axiom_trending — excluded from the sticky persist-allowlist) are evaluated EVERY
# cycle (they're in pair_by_addr, that's where their buys come from) but were never
# in _sticky_watchlist, so the arm set (built only from sticky) never armed them.
# FIX: arm from the UNION of self._cycle_pair_by_addr (this cycle's evaluated pairs)
# AND _sticky_watchlist. armed ⊇ bought becomes guaranteed (bought ⊆ evaluated).

def _pair(pc_h1=-10.0, vol_h1=1.0):
    return {"marketCap": 300_000, "liquidity": {"usd": 40_000},
            "pairCreatedAt": 1, "priceChange": {"h1": pc_h1},
            "volume": {"h1": vol_h1}}


def test_fast_arm_subset_arms_token_only_in_pair_by_addr(monkeypatch):
    """The GTAVI/VSK case: a token evaluated this cycle from a non-persisted source
    (in self._cycle_pair_by_addr only, NOT in _sticky_watchlist) is NOW armed."""
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "500")
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    s, now_ms = _scanner_for_lane_arm({})        # empty sticky
    s._sticky_watchlist = {}
    s._cycle_pair_by_addr = {"GTAVI": _pair(), "VSK": _pair()}
    s._fast_arm_subset(cfg, now_ms)
    armed = {k.lower() for k in s._fast_armed.keys()}
    assert "gtavi" in armed                      # was missed; now armed (lowercased)
    assert "vsk" in armed


def test_fast_arm_subset_arms_token_only_in_sticky(monkeypatch):
    """A token only in sticky (not in this cycle's pair_by_addr) is still armed."""
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "500")
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    s, now_ms = _scanner_for_lane_arm({})
    s._sticky_watchlist = {"STICKYONLY": {"pair": _pair()}}
    s._cycle_pair_by_addr = {"GTAVI": _pair()}
    s._fast_arm_subset(cfg, now_ms)
    armed = {k.lower() for k in s._fast_armed.keys()}
    assert "stickyonly" in armed
    assert "gtavi" in armed


def test_fast_arm_subset_dedups_token_in_both(monkeypatch):
    """A token in BOTH sources is armed exactly once (dedup by lowercased addr)."""
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "500")
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    s, now_ms = _scanner_for_lane_arm({})
    s._sticky_watchlist = {"BOTH": {"pair": _pair(vol_h1=1.0)}}
    s._cycle_pair_by_addr = {"BOTH": _pair(vol_h1=2.0)}
    s._fast_arm_subset(cfg, now_ms)
    keys = [k.lower() for k in s._fast_armed.keys()]
    assert keys.count("both") == 1               # armed exactly once (original-case key, case-insens check)


def test_fast_arm_subset_prefers_live_pair_when_in_both(monkeypatch):
    """When a token is in both, the LIVE cycle pair_by_addr entry (fresher) is the
    pair stored in _fast_armed, not the cached sticky one."""
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "500")
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    s, now_ms = _scanner_for_lane_arm({})
    sticky_pair = _pair(vol_h1=1.0); sticky_pair["_src"] = "sticky"
    live_pair = _pair(vol_h1=1.0);  live_pair["_src"] = "live"
    s._sticky_watchlist = {"BOTH": {"pair": sticky_pair}}
    s._cycle_pair_by_addr = {"BOTH": live_pair}
    s._fast_arm_subset(cfg, now_ms)
    assert {k.lower(): v for k, v in s._fast_armed.items()}["both"]["_src"] == "live"   # fresher live pair preferred (original-case key)


def test_fast_arm_subset_empty_or_none_pair_not_armed_in_union(monkeypatch):
    """An evaluated entry with an empty/None pair is not armed (in_band=bool(pair))."""
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "500")
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    s, now_ms = _scanner_for_lane_arm({})
    s._sticky_watchlist = {}
    s._cycle_pair_by_addr = {"GOOD": _pair(), "EMPTY": {}, "NONE": None}
    s._fast_arm_subset(cfg, now_ms)
    armed = {k.lower() for k in s._fast_armed.keys()}
    assert "good" in armed
    assert "empty" not in armed
    assert "none" not in armed


def test_fast_arm_subset_union_caps_and_volume_ranks(monkeypatch):
    """Volume ranking + armed_max cap hold across the UNION (cap=2 -> top-2 by vol)."""
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "2")
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    s, now_ms = _scanner_for_lane_arm({})
    s._sticky_watchlist = {"S1": {"pair": _pair(vol_h1=1.0)},
                           "S2": {"pair": _pair(vol_h1=2.0)}}
    s._cycle_pair_by_addr = {"C1": _pair(vol_h1=9.0), "C2": _pair(vol_h1=8.0)}
    s._fast_arm_subset(cfg, now_ms)
    assert {k.lower() for k in s._fast_armed.keys()} == {"c1", "c2"}   # top-2 by volume across union


def test_fast_arm_subset_default_armed_max_is_500(monkeypatch):
    """Default armed_max ceiling is 500 (the union ~480-token universe fits)."""
    monkeypatch.delenv("FAST_WATCH_ARMED_MAX", raising=False)
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    assert cfg.armed_max == 500
    s, now_ms = _scanner_for_lane_arm({})
    s._sticky_watchlist = {}
    s._cycle_pair_by_addr = {f"T{i}": _pair(vol_h1=float(i)) for i in range(600)}
    s._fast_arm_subset(cfg, now_ms)
    assert len(s._fast_armed) == 500              # clamped to default ceiling


def test_fast_arm_subset_falls_back_to_sticky_when_cycle_unset(monkeypatch):
    """First cycle: self._cycle_pair_by_addr unset -> fall back to sticky only,
    no crash."""
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "500")
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    s, now_ms = _scanner_for_lane_arm({})
    s._sticky_watchlist = {"STICKYONLY": {"pair": _pair()}}
    # do NOT set s._cycle_pair_by_addr at all
    assert not hasattr(s, "_cycle_pair_by_addr")
    s._fast_arm_subset(cfg, now_ms)               # must not raise
    assert "stickyonly" in {k.lower() for k in s._fast_armed.keys()}


# ──────────────────────────────────────────────────────────────────────────────
# CONCURRENT FAST-WATCH TICK (2026-06-20) — unblock-the-loop / faster-fills perf
# ──────────────────────────────────────────────────────────────────────────────
# Pure/structural pieces of the concurrent tick: (a) survivor cap+prioritization,
# (b) the chunk-parallelization helper, (c) the cache-only chart decision.

def test_chunk_addrs_splits_into_bounded_chunks():
    assert fw.chunk_addrs([], 50) == []
    assert fw.chunk_addrs(["a"], 50) == [["a"]]
    addrs = [f"M{i}" for i in range(120)]
    chunks = fw.chunk_addrs(addrs, 50)
    assert [len(c) for c in chunks] == [50, 50, 20]   # 120 -> 50/50/20
    # round-trips (order preserved, nothing dropped/dup'd)
    assert [a for c in chunks for a in c] == addrs


def test_chunk_addrs_bad_chunk_size_falls_back():
    addrs = ["a", "b", "c"]
    # zero / negative chunk size -> one chunk (never an infinite loop / crash)
    assert fw.chunk_addrs(addrs, 0) == [addrs]
    assert fw.chunk_addrs(addrs, -5) == [addrs]


def test_cap_survivors_returns_all_when_under_cap():
    samples = {"A": [1.0, 0.90], "B": [1.0, 1.04]}
    survivors = [("A", {"x": 1}), ("B", {"x": 2})]
    capped, was_capped = fw.cap_survivors(survivors, samples, max_n=20,
                                          dip_pct=3.0, rise_pct=3.0)
    assert was_capped is False
    assert [a for a, _ in capped] == ["A", "B"]   # unchanged order under cap


def test_cap_survivors_keeps_biggest_movers_first():
    # |move|: A=10% dip, B=4% rise, C=30% dip, D=5% rise. Keep top-2 by |move|.
    samples = {
        "A": [1.0, 0.90],          # -10%
        "B": [1.0, 1.04],          # +4%
        "C": [1.0, 0.70],          # -30%
        "D": [1.0, 1.05],          # +5%
    }
    survivors = [("A", {}), ("B", {}), ("C", {}), ("D", {})]
    capped, was_capped = fw.cap_survivors(survivors, samples, max_n=2,
                                          dip_pct=3.0, rise_pct=3.0)
    assert was_capped is True
    assert [a for a, _ in capped] == ["C", "A"]   # 30% dip, then 10% dip


def test_cap_survivors_zero_or_negative_cap_is_noop():
    samples = {"A": [1.0, 0.90]}
    survivors = [("A", {})]
    capped, was_capped = fw.cap_survivors(survivors, samples, max_n=0,
                                          dip_pct=3.0, rise_pct=3.0)
    assert was_capped is False
    assert capped == survivors            # 0 cap -> disabled (no cap)


def test_cap_survivors_missing_samples_sort_last():
    samples = {"A": [1.0, 0.95]}          # -5% ; B has no samples
    survivors = [("B", {}), ("A", {})]
    capped, was_capped = fw.cap_survivors(survivors, samples, max_n=1,
                                          dip_pct=3.0, rise_pct=3.0)
    assert was_capped is True
    assert [a for a, _ in capped] == ["A"]   # the one with a real move kept


def test_cache_only_charts_flag_defaults_on(monkeypatch):
    monkeypatch.delenv("FAST_WATCH_CACHE_ONLY_CHARTS", raising=False)
    assert fw.cache_only_charts_enabled() is True
    monkeypatch.setenv("FAST_WATCH_CACHE_ONLY_CHARTS", "off")
    assert fw.cache_only_charts_enabled() is False
    monkeypatch.setenv("FAST_WATCH_CACHE_ONLY_CHARTS", "on")
    assert fw.cache_only_charts_enabled() is True


def test_eval_concurrency_from_env(monkeypatch):
    monkeypatch.delenv("FAST_WATCH_EVAL_CONCURRENCY", raising=False)
    assert fw.eval_concurrency() == 5
    monkeypatch.setenv("FAST_WATCH_EVAL_CONCURRENCY", "9")
    assert fw.eval_concurrency() == 9
    monkeypatch.setenv("FAST_WATCH_EVAL_CONCURRENCY", "0")
    assert fw.eval_concurrency() == 1       # floor of 1
    monkeypatch.setenv("FAST_WATCH_EVAL_CONCURRENCY", "junk")
    assert fw.eval_concurrency() == 5       # bad -> default


def test_price_concurrency_from_env(monkeypatch):
    monkeypatch.delenv("FAST_WATCH_PRICE_CONCURRENCY", raising=False)
    assert fw.price_concurrency() == 4
    monkeypatch.setenv("FAST_WATCH_PRICE_CONCURRENCY", "2")
    assert fw.price_concurrency() == 2
    monkeypatch.setenv("FAST_WATCH_PRICE_CONCURRENCY", "0")
    assert fw.price_concurrency() == 1
    monkeypatch.setenv("FAST_WATCH_PRICE_CONCURRENCY", "junk")
    assert fw.price_concurrency() == 4


def test_max_survivors_per_tick_from_env(monkeypatch):
    monkeypatch.delenv("FAST_WATCH_MAX_SURVIVORS_PER_TICK", raising=False)
    assert fw.max_survivors_per_tick() == 20
    monkeypatch.setenv("FAST_WATCH_MAX_SURVIVORS_PER_TICK", "5")
    assert fw.max_survivors_per_tick() == 5
    monkeypatch.setenv("FAST_WATCH_MAX_SURVIVORS_PER_TICK", "junk")
    assert fw.max_survivors_per_tick() == 20


def test_price_timeout_secs_from_env(monkeypatch):
    """FAST_WATCH_PRICE_TIMEOUT_S: SHORT per-call price GET timeout so a 429/
    stalled chunk fails fast (skip the tick, retry ~3s later) instead of blocking
    near the old 8s ceiling. Default 4.0, floor 1.0, bad/empty -> default."""
    monkeypatch.delenv("FAST_WATCH_PRICE_TIMEOUT_S", raising=False)
    assert fw.price_timeout_secs() == 4.0
    monkeypatch.setenv("FAST_WATCH_PRICE_TIMEOUT_S", "3")
    assert fw.price_timeout_secs() == 3.0
    monkeypatch.setenv("FAST_WATCH_PRICE_TIMEOUT_S", "0.1")
    assert fw.price_timeout_secs() == 1.0       # floored at 1.0
    monkeypatch.setenv("FAST_WATCH_PRICE_TIMEOUT_S", "junk")
    assert fw.price_timeout_secs() == 4.0        # bad -> default


# ── Integration: the concurrent tick still escalates+fires for triggering tokens

def _scanner_for_concurrent_tick(n_survivors):
    """Scanner whose armed set has n tokens that ALL dip (so all become
    survivors), wired to record the eval order + observe concurrency."""
    from feeds.dip_scanner import DipScanner
    from collections import deque
    s = DipScanner.__new__(DipScanner)
    s._buy_fire_lock = asyncio.Lock()
    s._token_registry = None
    s._fast_watch_regime = {"_regime_n": 0, "_regime_dip_breadth_pct": None,
                            "_regime_h1_neg_pct": None}
    s._fast_armed = {
        f"T{i}": {"pairAddress": f"P{i}", "priceUsd": "1",
                  "volume": {"h1": float(n_survivors - i)}}
        for i in range(n_survivors)
    }
    s._fast_samples = {f"T{i}": deque([1.00], maxlen=40) for i in range(n_survivors)}
    s._fast_samples_ts = {}
    s._fw_tick_n = 0
    s._fw_stats = {"armed_hits": 0, "armed_misses": 0, "by_bot": {},
                   "last_tick": {}, "ticks": 0, "would_fire": 0}

    # All tokens dip 10% so all trigger move_fires.
    async def fake_batch(addrs):
        return {a.lower(): 0.90 for a in addrs}
    s._fast_batch_prices = fake_batch

    class _T:
        async def _get_token_price(self, a, pair_address=""):
            return None     # no pinned price -> use jupiter fresh
    s.trader = _T()

    s.evaluated = []
    conc = {"running": 0, "peak": 0}
    s._conc = conc

    async def fake_eval(pair, ctx):
        conc["running"] += 1
        conc["peak"] = max(conc["peak"], conc["running"])
        await asyncio.sleep(0.01)
        s.evaluated.append(pair.get("pairAddress"))
        conc["running"] -= 1
        return (None, 0, False)
    s._evaluate_pair = fake_eval
    return s


def test_concurrent_tick_evaluates_all_survivors(monkeypatch):
    """All triggering tokens still get evaluated (buy-correctness: same set)."""
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    monkeypatch.setenv("FAST_WATCH_DIP_PCT", "3")
    monkeypatch.setenv("FAST_WATCH_FULL_POLL_EVERY", "1")   # poll full set
    monkeypatch.setenv("FAST_WATCH_HOT_MAX", "100")
    monkeypatch.setenv("FAST_WATCH_EVAL_CONCURRENCY", "5")
    monkeypatch.setenv("FAST_WATCH_MAX_SURVIVORS_PER_TICK", "100")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_concurrent_tick(8)
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    assert sorted(s.evaluated) == sorted(f"P{i}" for i in range(8))


def test_concurrent_tick_runs_evals_concurrently(monkeypatch):
    """Eval loop runs concurrently (peak>1) but bounded by the semaphore."""
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    monkeypatch.setenv("FAST_WATCH_DIP_PCT", "3")
    monkeypatch.setenv("FAST_WATCH_FULL_POLL_EVERY", "1")
    monkeypatch.setenv("FAST_WATCH_HOT_MAX", "100")
    monkeypatch.setenv("FAST_WATCH_EVAL_CONCURRENCY", "4")
    monkeypatch.setenv("FAST_WATCH_MAX_SURVIVORS_PER_TICK", "100")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_concurrent_tick(12)
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    assert s._conc["peak"] > 1               # concurrency happened
    assert s._conc["peak"] <= 4              # bounded by the semaphore


def test_concurrent_tick_threads_cache_only_flag(monkeypatch):
    """The tick threads FAST_WATCH_CACHE_ONLY_CHARTS into _evaluate_pair's ctx so
    the fast path can avoid cold GT OHLC fetches (the 429-storm cut)."""
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    monkeypatch.setenv("FAST_WATCH_DIP_PCT", "3")
    monkeypatch.setenv("FAST_WATCH_FULL_POLL_EVERY", "1")
    monkeypatch.setenv("FAST_WATCH_HOT_MAX", "100")
    monkeypatch.setenv("FAST_WATCH_CACHE_ONLY_CHARTS", "on")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_concurrent_tick(1)
    seen = {}
    async def cap_eval(pair, ctx):
        seen["cache_only"] = ctx.get("_fast_cache_only_charts")
        return (None, 0, False)
    s._evaluate_pair = cap_eval
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    assert seen["cache_only"] is True
    # flag off -> ctx carries False (cold-fetch path)
    monkeypatch.setenv("FAST_WATCH_CACHE_ONLY_CHARTS", "off")
    s2 = _scanner_for_concurrent_tick(1)
    seen2 = {}
    async def cap_eval2(pair, ctx):
        seen2["cache_only"] = ctx.get("_fast_cache_only_charts")
        return (None, 0, False)
    s2._evaluate_pair = cap_eval2
    asyncio.run(s2._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    assert seen2["cache_only"] is False


def test_concurrent_tick_caps_survivors(monkeypatch):
    """With more survivors than the per-tick cap, only the cap count is evaluated;
    the biggest movers are kept (here all dip equally, so just count is bounded)."""
    monkeypatch.setenv("FAST_WATCH_MODE", "shadow")
    monkeypatch.setenv("FAST_WATCH_DIP_PCT", "3")
    monkeypatch.setenv("FAST_WATCH_FULL_POLL_EVERY", "1")
    monkeypatch.setenv("FAST_WATCH_HOT_MAX", "100")
    monkeypatch.setenv("FAST_WATCH_EVAL_CONCURRENCY", "5")
    monkeypatch.setenv("FAST_WATCH_MAX_SURVIVORS_PER_TICK", "3")
    from core.fast_watch import FastWatchConfig, FastWatchDedup
    cfg = FastWatchConfig.from_env()
    s = _scanner_for_concurrent_tick(10)
    asyncio.run(s._fast_watch_tick(cfg, FastWatchDedup(cfg.eval_cooldown_secs)))
    assert len(s.evaluated) == 3             # capped at MAX_SURVIVORS_PER_TICK


# ──────────────────────────────────────────────────────────────────────────────
# FORWARD FILL-SPEED CAPTURE — pure logic (fast-entry-price vs sweep-entry-price)
# ──────────────────────────────────────────────────────────────────────────────

def test_fill_speed_delta_pct_basic():
    # sweep filled DEARER than the fast price -> positive delta (fast was cheaper)
    assert round(fw.fill_speed_delta_pct(100.0, 110.0), 6) == 10.0
    # sweep filled CHEAPER than fast -> negative (fast front-ran a further drop)
    assert round(fw.fill_speed_delta_pct(100.0, 90.0), 6) == -10.0
    assert fw.fill_speed_delta_pct(100.0, 100.0) == 0.0


def test_fill_speed_delta_pct_guards():
    assert fw.fill_speed_delta_pct(0.0, 100.0) is None      # bad fast price
    assert fw.fill_speed_delta_pct(-1.0, 100.0) is None
    assert fw.fill_speed_delta_pct(100.0, 0.0) is None      # bad sweep price
    assert fw.fill_speed_delta_pct(100.0, -5.0) is None
    assert fw.fill_speed_delta_pct(None, 100.0) is None
    assert fw.fill_speed_delta_pct(100.0, None) is None
    assert fw.fill_speed_delta_pct("x", 100.0) is None


def test_fill_speed_record_is_address_keyed_dict():
    rec = fw.fill_speed_record(
        token="BONK", bot="dip_buy",
        fast_price=100.0, fast_ts=1000.0,
        sweep_price=110.0, sweep_ts=1085.0,
        address="So111ADDR",
    )
    assert rec["token_address"] == "So111ADDR"   # ADDRESS-keyed, never symbol
    assert rec["symbol"] == "BONK"
    assert rec["bot"] == "dip_buy"
    assert rec["fast_price"] == 100.0
    assert rec["fast_ts"] == 1000.0
    assert rec["sweep_price"] == 110.0
    assert rec["sweep_ts"] == 1085.0
    assert rec["lead_secs"] == 85.0              # sweep_ts - fast_ts
    assert round(rec["delta_pct"], 6) == 10.0
    assert "ts" in rec


def test_fill_speed_record_handles_missing_ts_and_bad_prices():
    rec = fw.fill_speed_record(
        token="X", bot="b",
        fast_price=0.0, fast_ts=None,
        sweep_price=110.0, sweep_ts=None,
        address="A",
    )
    assert rec["lead_secs"] is None       # cannot compute without both ts
    assert rec["delta_pct"] is None       # bad fast price -> None delta


def test_realized_pair_edge():
    # fast entry 100, sweep entry 110, same exit 120
    fast_pnl, sweep_pnl, edge = fw.realized_pair(100.0, 110.0, 120.0)
    assert round(fast_pnl, 6) == 20.0
    assert round(sweep_pnl, 6) == round((120.0 / 110.0 - 1) * 100.0, 6)
    assert round(edge, 6) == round(fast_pnl - sweep_pnl, 6)
    assert edge > 0    # cheaper fast entry -> more P&L


def test_realized_pair_guards():
    assert fw.realized_pair(0.0, 110.0, 120.0) is None
    assert fw.realized_pair(100.0, 0.0, 120.0) is None
    assert fw.realized_pair(100.0, 110.0, 0.0) is None
    assert fw.realized_pair(None, 110.0, 120.0) is None
    assert fw.realized_pair(100.0, None, 120.0) is None
    assert fw.realized_pair(100.0, 110.0, None) is None
    assert fw.realized_pair(-1.0, 110.0, 120.0) is None
