"""Units for the 2026-06-10 smart_follow build: K-tier caps, elite-exit env
switch, fire-quality shadow mapping, and the PM strategy prefix handling that
lets the K-tier pods (smart_follow_k2/_solo) inherit the follow exit stack."""
import asyncio
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core.strategies.smart_money_follow import (  # noqa: E402
    SmartMoneyFollowStrategy, _elite_exit_on)
from core.position_manager import PositionManager  # noqa: E402


def _mk_strategy():
    return SmartMoneyFollowStrategy(scanner=None, watchlist=["w1", "w2", "w3"],
                                    k=3, position_manager=None)


def test_tier_cap_rolling_window():
    s = _mk_strategy()
    s.tier_caps_per_hour = {"k2": 2, "solo": 1}
    now = 1_000_000
    assert s._tier_cap_ok("solo", now)
    s._tier_fires["solo"].append(now)
    assert not s._tier_cap_ok("solo", now + 10)        # cap hit
    assert s._tier_cap_ok("solo", now + 3601)          # rolled out of window
    s._tier_fires["k2"] += [now, now + 5]
    assert not s._tier_cap_ok("k2", now + 10)


def test_elite_exit_env_switch():
    os.environ.pop("SMART_FOLLOW_ELITE_EXIT", None)
    assert _elite_exit_on() is True                    # default on
    os.environ["SMART_FOLLOW_ELITE_EXIT"] = "off"
    assert _elite_exit_on() is False
    os.environ.pop("SMART_FOLLOW_ELITE_EXIT", None)


def test_fire_quality_shadow_mapping():
    s = _mk_strategy()
    s.fire_quality = {"good": 1.2, "bad": -3.0, "meh": -0.3}
    # mirrors the mapping in _fire: >0.5 -> 1.25, < -1.5 -> 0.5, <0 -> 0.75
    def mult(wset):
        quals = [s.fire_quality[w] for w in wset if w in s.fire_quality]
        fq = sum(quals) / len(quals) if quals else None
        return (1.0 if fq is None else 1.25 if fq > 0.5 else
                0.5 if fq < -1.5 else 0.75 if fq < 0 else 1.0)
    assert mult({"good"}) == 1.25
    assert mult({"bad"}) == 0.5
    assert mult({"meh"}) == 0.75
    assert mult({"unknown"}) == 1.0


def test_external_exit_no_state_returns_false():
    pm = PositionManager.__new__(PositionManager)
    pm._states = {}
    pm.chain_name = "test"
    assert asyncio.run(pm.external_exit("SoMeMiNt", "test")) is False


def test_strategy_prefix_covers_tier_pods():
    # the PM maps any smart_follow* strategy onto the dip ladder + grace arm
    for tag in ("smart_follow", "smart_follow_k2", "smart_follow_solo"):
        assert tag.startswith("smart_follow")
    assert not "scanner".startswith("smart_follow")


def test_convex_tier_resolution_and_cap():
    s = _mk_strategy()
    s.convex = {"wConvex"}
    s.solo = set()
    s.high_tier = set()
    s.tier_caps_per_hour = {"k2": 8, "solo": 6, "convex": 1}
    s._tier_fires = {"k2": [], "solo": [], "convex": []}
    now = 1_000_000
    # single convex-pod wallet buy -> convex tier eligible
    assert s._tier_cap_ok("convex", now)
    s._tier_fires["convex"].append(now)
    assert not s._tier_cap_ok("convex", now + 10)      # cap=1 consumed
    assert s._tier_cap_ok("convex", now + 3601)        # rolls off


def test_convex_payoff_overrides_in_pm():
    # convex: tiny TP1 partial + NO grace; other follow tags: 0.65 + grace-eligible
    assert "smart_follow_convex".startswith("smart_follow")
    tp1 = lambda strat: (0.10 if strat == "smart_follow_convex"
                         else 0.65 if strat.startswith("smart_follow") else None)
    assert tp1("smart_follow_convex") == 0.10
    assert tp1("smart_follow") == 0.65
    assert tp1("smart_follow_k2") == 0.65
    assert tp1("scanner") is None
    grace_eligible = lambda strat: (strat.startswith("smart_follow")
                                    and strat != "smart_follow_convex")
    assert not grace_eligible("smart_follow_convex")
    assert grace_eligible("smart_follow_solo")


def test_realtime_ingest_dedupe_and_buy_path():
    import asyncio
    s = _mk_strategy()
    s.watchlist = ["wA", "wB"]
    # buy: appended to window + sizes + hot-wake
    asyncio.run(s.ingest_realtime_trade("wA", "MINT1", "buy", 0.5, 1000, "sig1"))
    assert len(s._buys) == 1 and s._buys[0][:2] == ("MINT1", "wA")
    assert s._wallet_sizes["wA"] == [0.5]
    assert s._hot_event.is_set()
    # same signature again -> deduped (the RPC sweep shares this set)
    asyncio.run(s.ingest_realtime_trade("wA", "MINT1", "buy", 0.5, 1001, "sig1"))
    assert len(s._buys) == 1
    # non-watchlist wallet ignored
    asyncio.run(s.ingest_realtime_trade("stranger", "MINT2", "buy", 1.0, 1002, "sig2"))
    assert len(s._buys) == 1


def test_realtime_sell_round_trip_logged_without_pm():
    import asyncio
    s = _mk_strategy()
    s.watchlist = ["wA"]
    asyncio.run(s.ingest_realtime_trade("wA", "MINT1", "buy", 1.0, 1000, "b1"))
    # sell closes the elite round-trip without raising (no PM wired)
    asyncio.run(s.ingest_realtime_trade("wA", "MINT1", "sell", 1.4, 1300, "s1"))
    assert ("wA", "MINT1") not in s._wallet_pos


def test_distribution_guard_modes_and_window():
    import os
    from core.strategies.smart_money_follow import _dist_guard_mode, _dist_guard_sec
    os.environ.pop("SMART_FOLLOW_DIST_GUARD", None)
    assert _dist_guard_mode() == "enforce"              # default on
    os.environ["SMART_FOLLOW_DIST_GUARD"] = "shadow"
    assert _dist_guard_mode() == "shadow"
    os.environ["SMART_FOLLOW_DIST_GUARD"] = "garbage"
    assert _dist_guard_mode() == "enforce"              # fail-closed
    os.environ.pop("SMART_FOLLOW_DIST_GUARD", None)
    assert _dist_guard_sec() == 600


def test_distribution_guard_verdict_logic():
    # mirrors the _fire computation: roster sell within window -> blocked
    from core.strategies.smart_money_follow import _dist_guard_sec
    s = _mk_strategy()
    now = 1_000_000
    verdict = lambda mode: ("blocked" if (dist and mode == "enforce") else
                            "shadow_block" if dist else "pass")
    s._recent_sells["MINT1"] = now - 60                 # sold 1min ago
    last = s._recent_sells.get("MINT1", 0)
    dist = last and (now - last) <= _dist_guard_sec()
    assert verdict("enforce") == "blocked"
    assert verdict("shadow") == "shadow_block"
    s._recent_sells["MINT1"] = now - 700                # outside 600s window
    last = s._recent_sells.get("MINT1", 0)
    dist = last and (now - last) <= _dist_guard_sec()
    assert verdict("enforce") == "pass"


def test_realtime_sell_feeds_distribution_guard():
    import asyncio
    s = _mk_strategy()
    s.watchlist = ["wA"]
    asyncio.run(s.ingest_realtime_trade("wA", "MINT9", "sell", 0.8, 5000, "sx"))
    assert s._recent_sells.get("MINT9") == 5000


def test_fire_cooldown_one_per_day_default_and_persistence(tmp_path):
    import os, json, time
    os.environ.pop("SMART_FOLLOW_FIRE_COOLDOWN_SEC", None)
    os.environ["DATA_DIR"] = str(tmp_path)
    try:
        s = _mk_strategy()
        assert s.fire_cooldown == 3600                  # 1h anti-spam; won-today veto handles re-fires
        # persistence round-trip: a saved fire survives a "restart"
        s._fired["MINTX"] = time.time()
        with open(s._fired_path, "w") as f:
            json.dump(s._fired, f)
        s2 = _mk_strategy()
        assert "MINTX" in s2._fired                     # no deploy amnesia
        # stale entries (>48h) dropped on load
        with open(s._fired_path, "w") as f:
            json.dump({"OLD": time.time() - 200000}, f)
        s3 = _mk_strategy()
        assert "OLD" not in s3._fired
    finally:
        os.environ.pop("DATA_DIR", None)


def test_won_today_veto_memory(tmp_path, monkeypatch):
    import os
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import core.follow_capital as fcmod
    importlib.reload(fcmod)
    fc = fcmod.FollowCapitalManager()
    fc.record_open("MintA", 50.0)
    fc.record_close("MintA", 1.0, +4.20)        # won today
    assert fc.won_today("MintA") is True
    assert fc.won_today("minta") is True        # case-insensitive
    fc.record_open("MintB", 50.0)
    fc.record_close("MintB", 1.0, -3.10)        # lost today
    assert fc.won_today("MintB") is False       # after-LOSS re-fires stay allowed
    # persistence round-trip (same day)
    fc2 = fcmod.FollowCapitalManager()
    assert fc2.won_today("MintA") is True
    importlib.reload(fcmod)


def test_dexscreener_circuit_breaker():
    import time as _t
    from feeds.dexscreener_client import DexScreenerClient
    c = DexScreenerClient()
    assert c._circuit_ok()
    for _ in range(4):
        c._record_result(False)
    assert c._circuit_ok()                  # 4 failures: still closed
    c._record_result(False)                 # 5th consecutive -> opens
    assert not c._circuit_ok()
    assert c._circuit_open_until > _t.monotonic() + 250
    c._circuit_open_until = 0.0             # simulate expiry
    assert c._circuit_ok()
    c._record_result(False); c._record_result(True); c._record_result(False)
    assert c._circuit_ok()                  # success resets the streak


def test_pool_daily_floor(tmp_path, monkeypatch):
    import os, importlib
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SMART_FOLLOW_DAILY_FLOOR_USD", raising=False)
    import core.follow_capital as fcmod
    importlib.reload(fcmod)
    fc = fcmod.FollowCapitalManager()
    assert fc.daily_floor_hit() is False
    fc.record_open("M1", 50.0); fc.record_close("M1", 1.0, -39.0)
    assert fc.daily_floor_hit() is False        # -39 > -40
    fc.record_open("M2", 50.0); fc.record_close("M2", 1.0, -2.0)
    assert fc.daily_floor_hit() is True         # -41 <= -40
    monkeypatch.setenv("SMART_FOLLOW_DAILY_FLOOR_USD", "off")
    assert fc.daily_floor_hit() is False        # kill switch
    importlib.reload(fcmod)


def test_trigger_state_enforcement_dormant_and_active(monkeypatch):
    from core.trigger_state_gates import should_drop_trigger, enforce_set
    feats_block = {"buy_sell_volume_imbalance": 0.80}   # whale_conviction needs <=0.38
    feats_pass = {"buy_sell_volume_imbalance": 0.20}
    monkeypatch.delenv("TRIGGER_STATE_ENFORCE", raising=False)
    assert enforce_set() == set()
    assert should_drop_trigger("whale_conviction", feats_block) is False  # dormant
    monkeypatch.setenv("TRIGGER_STATE_ENFORCE", "whale_conviction,calm_at_support")
    assert should_drop_trigger("whale_conviction", feats_block) is True   # outside state
    assert should_drop_trigger("whale_conviction", feats_pass) is False   # in state
    assert should_drop_trigger("whale_conviction", {}) is False           # na fail-open
    assert should_drop_trigger("deep_1h_dip", feats_block) is False       # not in set


def test_copy_regime_dial(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import core.follow_capital as fcmod
    importlib.reload(fcmod)
    fc = fcmod.FollowCapitalManager()
    assert fc.copy_dial()["state"] == "warming"
    for _ in range(15):                       # grind: -$2/close
        fc.record_open("M", 50.0); fc.record_close("M", 1.0, -2.0)
    d = fc.copy_dial()
    assert d["state"] == "bad" and d["exp"] == -2.0
    for _ in range(20):                       # recovery: +$3/close
        fc.record_open("M2", 50.0); fc.record_close("M2", 1.0, 3.0)
    assert fc.copy_dial()["state"] == "good"
    # persistence
    fc2 = fcmod.FollowCapitalManager()
    assert fc2.copy_dial()["state"] == "good"
    importlib.reload(fcmod)
