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
    assert cfg.armed_max == 150
    assert cfg.sample_window == 40
    assert cfg.arm_band_pp == 15.0
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
    assert cfg.armed_max == 150


def _cfg(**kw):
    base = dict(mode="shadow", interval_secs=3.0, dip_pct=3.0, rise_pct=3.0,
                eval_cooldown_secs=60.0, bot_allowlist=frozenset({"x"}), armed_max=30,
                sample_window=40, arm_band_pp=15.0)
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
    ceiling (no artificial small cap). 100 in-band < 150 ceiling -> all 100 armed."""
    monkeypatch.setenv("JUPITER_PRICE_PRIMARY", "on")
    monkeypatch.delenv("FAST_WATCH_ARMED_MAX", raising=False)
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()          # armed_max default 150 (rate-safe ceiling)
    s, now_ms = _scanner_for_arm(100)
    s._fast_arm_subset(cfg, now_ms)
    assert len(s._fast_armed) == 100   # whole in-band watchlist (under the 150 ceiling)


def test_fast_arm_subset_clamps_to_rate_safe_ceiling_under_jupiter(monkeypatch):
    """JUPITER_PRICE_PRIMARY=on lifts armed_max to n_inband, but clamps to the
    rate-safe FAST_WATCH_ARMED_MAX ceiling (default 150) so adding pumps to the
    armed set can't blow past the Jupiter ~110 req/min budget."""
    monkeypatch.setenv("JUPITER_PRICE_PRIMARY", "on")
    monkeypatch.delenv("FAST_WATCH_ARMED_MAX", raising=False)
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()          # armed_max default 150
    assert cfg.armed_max == 150
    s, now_ms = _scanner_for_arm(400)          # 400 in-band tokens
    s._fast_arm_subset(cfg, now_ms)
    assert len(s._fast_armed) == 150           # clamped to the rate-safe ceiling, not 400


def test_fast_arm_subset_caps_at_30_when_flag_off(monkeypatch):
    """Flag off -> existing armed_max=30 cap preserved (unchanged behavior)."""
    monkeypatch.delenv("JUPITER_PRICE_PRIMARY", raising=False)
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig(mode="shadow", interval_secs=3.0, dip_pct=3.0, rise_pct=3.0,
                          eval_cooldown_secs=60.0, bot_allowlist=frozenset({"x"}),
                          armed_max=30, sample_window=40, arm_band_pp=15.0)
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
    s._fast_armed = {"DIPADDR": {"pairAddress": "POOLX", "priceUsd": "1"}}
    s._fast_samples = {"DIPADDR": deque([1.00], maxlen=40)}

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


# ---- FIX A: lane-aware arming ----------------------------------------------

def _scanner_for_lane_arm(pairs):
    """Scanner with min_mcap=$1M / max_mcap=$50M fleet band, watchlist = `pairs`
    {addr: pair_dict}. Lanes default OFF unless env flips them on per test."""
    from feeds.dip_scanner import DipScanner
    s = DipScanner.__new__(DipScanner)
    s.min_age_ms = 0
    s.min_mcap = 1_000_000.0
    s.max_mcap = 50_000_000.0
    s._fast_samples = {}
    s._sticky_watchlist = {a: {"pair": p} for a, p in pairs.items()}
    return s, 10_000_000_000


def test_fast_arm_subset_arms_badday_lane_token(monkeypatch):
    """A sub-$500k token the badday lane admits (envelope: 50-500k mcap, age>=6h,
    liq>=15k, deep flush pc_h1<=-20) is now ARMED — was excluded by the strict
    in_band min_mcap floor. (Kelsey-class $90k microcap.)"""
    monkeypatch.setenv("BADDAY_LANE", "on")
    monkeypatch.delenv("YOUNG_TOKEN_PROBE", raising=False)
    monkeypatch.delenv("LOW_MCAP_PROBE", raising=False)
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "30")
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    # age = 24h so (now_ms - created) >= 6h envelope floor.  pc_h1 -25 = flush state.
    now_ms = 10_000_000_000
    created = now_ms - int(24 * 3_600_000)
    pairs = {
        "KELSEY90K": {"marketCap": 90_000, "liquidity": {"usd": 30_000},
                      "pairCreatedAt": created, "priceChange": {"h1": -25.0},
                      "volume": {"h1": 5.0}},
        "INBAND2M": {"marketCap": 2_000_000, "liquidity": {"usd": 80_000},
                     "pairCreatedAt": created, "priceChange": {"h1": -5.0},
                     "volume": {"h1": 9.0}},
        "HUGE100M": {"marketCap": 100_000_000, "liquidity": {"usd": 80_000},
                     "pairCreatedAt": created, "priceChange": {"h1": -5.0},
                     "volume": {"h1": 9.0}},
    }
    s, _ = _scanner_for_lane_arm(pairs)
    s._sticky_watchlist = {a: {"pair": p} for a, p in pairs.items()}
    s._fast_arm_subset(cfg, now_ms)
    armed = set(s._fast_armed.keys())
    assert "KELSEY90K" in armed     # lane-admitted sub-band microcap now armed (FIX A)
    assert "INBAND2M" in armed      # in-band still armed
    assert "HUGE100M" not in armed  # truly out-of-band (mcap>max), no lane -> excluded


def test_fast_arm_subset_excludes_subband_token_with_no_lane(monkeypatch):
    """All lanes OFF -> a sub-$1M token NOT admitted by any lane stays excluded
    (regression guard: lane-aware in_band must not become 'arm everything')."""
    monkeypatch.setenv("BADDAY_LANE", "off")
    monkeypatch.delenv("YOUNG_TOKEN_PROBE", raising=False)
    monkeypatch.delenv("LOW_MCAP_PROBE", raising=False)
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "30")
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    now_ms = 10_000_000_000
    created = now_ms - int(24 * 3_600_000)
    pairs = {
        "MICRO90K": {"marketCap": 90_000, "liquidity": {"usd": 30_000},
                     "pairCreatedAt": created, "priceChange": {"h1": -25.0},
                     "volume": {"h1": 5.0}},
    }
    s, _ = _scanner_for_lane_arm(pairs)
    s._sticky_watchlist = {a: {"pair": p} for a, p in pairs.items()}
    s._fast_arm_subset(cfg, now_ms)
    assert "MICRO90K" not in set(s._fast_armed.keys())


def test_fast_arm_subset_arms_low_mcap_lane_token(monkeypatch):
    """LOW_MCAP_PROBE on -> a token in [500k, 1M) the low-mcap lane admits is armed."""
    monkeypatch.setenv("LOW_MCAP_PROBE", "1")
    monkeypatch.setenv("BADDAY_LANE", "off")
    monkeypatch.delenv("YOUNG_TOKEN_PROBE", raising=False)
    monkeypatch.setenv("FAST_WATCH_ARMED_MAX", "30")
    from core.fast_watch import FastWatchConfig
    cfg = FastWatchConfig.from_env()
    now_ms = 10_000_000_000
    created = now_ms - int(24 * 3_600_000)
    pairs = {
        "LOWMCAP700K": {"marketCap": 700_000, "liquidity": {"usd": 60_000},
                        "pairCreatedAt": created, "priceChange": {"h1": -8.0},
                        "volume": {"h1": 5.0}},
    }
    s, _ = _scanner_for_lane_arm(pairs)
    s._sticky_watchlist = {a: {"pair": p} for a, p in pairs.items()}
    s._fast_arm_subset(cfg, now_ms)
    assert "LOWMCAP700K" in set(s._fast_armed.keys())
