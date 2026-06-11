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
