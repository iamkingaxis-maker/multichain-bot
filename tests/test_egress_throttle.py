"""Egress burst-control budget for heavy /api payloads (dashboard/web_dashboard).
2026-06-04: full=1/all=1/universe-recorder heavy pulls are budget-gated so a
looping / fan-out consumer can't pull tens of MB repeatedly (Railway cap breach)."""
import os
import importlib


def _fresh(min_interval="0", max_per_hour="3"):
    os.environ["EGRESS_HEAVY_MIN_INTERVAL_SECS"] = min_interval
    os.environ["EGRESS_HEAVY_MAX_PER_HOUR"] = max_per_hour
    import dashboard.web_dashboard as w
    importlib.reload(w)
    w._HEAVY_SERVES.clear()
    return w


def test_budget_caps_heavy_serves():
    w = _fresh(min_interval="0", max_per_hour="3")
    res = [w._egress_allow_heavy() for _ in range(6)]
    assert res == [True, True, True, False, False, False]


def test_min_interval_blocks_back_to_back():
    # huge min-interval -> only the first serve passes, rest downgraded
    w = _fresh(min_interval="9999", max_per_hour="100")
    res = [w._egress_allow_heavy() for _ in range(4)]
    assert res == [True, False, False, False]


def test_defaults_are_sane():
    os.environ.pop("EGRESS_HEAVY_MIN_INTERVAL_SECS", None)
    os.environ.pop("EGRESS_HEAVY_MAX_PER_HOUR", None)
    import dashboard.web_dashboard as w
    importlib.reload(w)
    mi, mph = w._egress_heavy_cfg()
    assert mi == 10.0 and mph == 20  # conservative defaults
    # a single occasional heavy pull always passes
    w._HEAVY_SERVES.clear()
    assert w._egress_allow_heavy() is True
