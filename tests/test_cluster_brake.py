import os
import pytest
from core.cluster_brake import (
    cluster_brake_mode, cluster_brake_multiplier, fleet_holders,
)
from core.per_bot_position_manager import PerBotPositionManager
from core.bot_config import BotConfig


def _cfg(bot_id="b"):
    return BotConfig(bot_id=bot_id, display_name=bot_id, max_concurrent_positions=50)


# ── multiplier curve (calibrated to realized EV-by-swarm buckets) ──────────
@pytest.mark.parametrize("holders,expected", [
    (0, 1.0), (1, 1.0), (4, 1.0),      # solo/low — untouched
    (5, 0.6), (9, 0.6),                # 5-9
    (10, 0.5), (19, 0.5),              # 10-19
    (20, 0.3), (50, 0.3),              # 20+ catastrophic
])
def test_multiplier_curve(holders, expected):
    assert cluster_brake_multiplier(holders) == expected


def test_multiplier_monotone_non_increasing():
    vals = [cluster_brake_multiplier(h) for h in range(0, 40)]
    assert all(a >= b for a, b in zip(vals, vals[1:]))


# ── mode resolution (env CLUSTER_BRAKE_MODE) ───────────────────────────────
def test_mode_default_is_shadow(monkeypatch):
    monkeypatch.delenv("CLUSTER_BRAKE_MODE", raising=False)
    assert cluster_brake_mode() == "shadow"


@pytest.mark.parametrize("val,expected", [
    ("off", "off"), ("shadow", "shadow"), ("enforce", "enforce"),
    ("ENFORCE", "enforce"), (" off ", "off"), ("garbage", "shadow"),
])
def test_mode_resolution(monkeypatch, val, expected):
    monkeypatch.setenv("CLUSTER_BRAKE_MODE", val)
    assert cluster_brake_mode() == expected


# ── fleet_holders counts same-token open exposure, excludes the entrant ────
def test_fleet_holders_counts_and_excludes():
    pms = {f"b{i}": PerBotPositionManager(_cfg(f"b{i}")) for i in range(4)}
    # b0, b1, b2 hold TOKE; b3 does not
    for bid in ("b0", "b1", "b2"):
        pms[bid].open_position("TOKE", 0.001, 20.0, entry_time=1.0)
    # entrant b3 joining TOKE sees 3 other holders
    assert fleet_holders(pms, "TOKE", exclude_bot="b3") == 3
    # entrant b0 (already holds) is excluded -> sees the other 2
    assert fleet_holders(pms, "TOKE", exclude_bot="b0") == 2
    # a token nobody holds
    assert fleet_holders(pms, "NONE", exclude_bot="b3") == 0


def test_fleet_holders_no_exclude():
    pms = {f"b{i}": PerBotPositionManager(_cfg(f"b{i}")) for i in range(3)}
    pms["b0"].open_position("X", 0.001, 20.0, entry_time=1.0)
    pms["b1"].open_position("X", 0.001, 20.0, entry_time=1.0)
    assert fleet_holders(pms, "X") == 2
