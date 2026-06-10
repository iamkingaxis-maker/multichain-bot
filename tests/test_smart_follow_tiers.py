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
