# tests/test_fw_stats.py
"""Unit tests for the fast-watch observability counters (_fw_stats) and the
hit-rate math behind GET /api/fast-watch. Read-only/observability only — these
counters must NEVER break the buy or tick path, so increments are exercised
against bad input too.

We bind the real DipScanner methods onto a lightweight stand-in object to avoid
the heavy DipScanner.__init__ (network/config); the methods only touch
self._fw_stats, so this is a faithful test of the increment logic.
"""
import types
from feeds.dip_scanner import DipScanner


def _fresh_stats():
    return {
        "armed_hits": 0,
        "armed_misses": 0,
        "by_bot": {},
        "last_tick": {"armed": 0, "polled": 0, "fired": 0, "mode": "off", "ts": 0},
        "ticks": 0,
        "would_fire": 0,
    }


def _stub():
    obj = types.SimpleNamespace()
    obj._fw_stats = _fresh_stats()
    # Bind the unbound methods so they run against our stub's _fw_stats.
    obj._fw_record_hit = types.MethodType(DipScanner._fw_record_hit, obj)
    obj._fw_record_tick = types.MethodType(DipScanner._fw_record_tick, obj)
    return obj


def test_record_hit_increments_global_and_per_bot():
    obj = _stub()
    obj._fw_record_hit("badday_flush", True)
    obj._fw_record_hit("badday_flush", True)
    obj._fw_record_hit("badday_flush", False)
    obj._fw_record_hit("legacy_dip", False)

    st = obj._fw_stats
    assert st["armed_hits"] == 2
    assert st["armed_misses"] == 2
    assert st["by_bot"]["badday_flush"] == {"hits": 2, "misses": 1}
    assert st["by_bot"]["legacy_dip"] == {"hits": 0, "misses": 1}


def test_hit_rate_math():
    # 3 hits / (3 hits + 1 miss) = 0.75
    stats = {"armed_hits": 3, "armed_misses": 1}
    assert DipScanner.fw_hit_rate(stats) == 0.75
    # denom == 0 -> None (no samples yet)
    assert DipScanner.fw_hit_rate({"armed_hits": 0, "armed_misses": 0}) is None
    # all misses -> 0.0
    assert DipScanner.fw_hit_rate({"armed_hits": 0, "armed_misses": 5}) == 0.0


def test_record_tick_updates_last_tick_and_counts():
    obj = _stub()
    obj._fw_record_tick(10, 8, 2, "shadow", 1234.5, would_fire=2)
    obj._fw_record_tick(0, 0, 0, "shadow", 1235.0)

    st = obj._fw_stats
    assert st["ticks"] == 2
    assert st["would_fire"] == 2
    assert st["last_tick"] == {
        "armed": 0, "polled": 0, "fired": 0, "mode": "shadow", "ts": 1235.0,
        # TIERED POLL (hot-subset vs full-armed) counters — default 0
        "hot": 0, "full": 0,
    }


def test_counters_are_exception_safe():
    # A broken stats dict (None) must not raise — an obs counter can never
    # break the buy/tick path.
    obj = types.SimpleNamespace()
    obj._fw_stats = None
    obj._fw_record_hit = types.MethodType(DipScanner._fw_record_hit, obj)
    obj._fw_record_tick = types.MethodType(DipScanner._fw_record_tick, obj)
    # Should swallow internally and not raise.
    obj._fw_record_hit("x", True)
    obj._fw_record_tick(1, 1, 1, "off", 0.0)
    # Bad input to the static hit-rate helper -> None, no raise.
    assert DipScanner.fw_hit_rate(None) is None
    assert DipScanner.fw_hit_rate({"armed_hits": "bad"}) is None
