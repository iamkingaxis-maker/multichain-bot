# tests/test_tape_cache.py — warm tape cache contract (2026-07-05)
from core.fast_watch import tape_cache_get, tape_cache_put


def test_fresh_hit_and_stale_miss():
    c = {}
    tape_cache_put(c, "pairA", [{"kind": "buy"}], now_mono=100.0)
    assert tape_cache_get(c, "pairA", 130.0, max_age_secs=45) == [{"kind": "buy"}]
    assert tape_cache_get(c, "pairA", 146.1, max_age_secs=45) is None  # stale


def test_empty_trades_never_hit():
    c = {}
    tape_cache_put(c, "pairA", [], now_mono=100.0)
    assert tape_cache_get(c, "pairA", 101.0, max_age_secs=45) is None


def test_miss_on_absent_key_and_garbage():
    assert tape_cache_get({}, "x", 0.0, 45) is None
    assert tape_cache_get(None, "x", 0.0, 45) is None
    assert tape_cache_get({"x": "garbage"}, "x", 0.0, 45) is None


def test_eviction_keeps_newest():
    c = {}
    for i in range(70):
        tape_cache_put(c, f"p{i}", [{"i": i}], now_mono=float(i), max_entries=64)
    assert len(c) == 64
    assert "p0" not in c and "p69" in c


def test_put_ignores_none_trades_and_empty_key():
    c = {}
    tape_cache_put(c, "", [1], 0.0)
    tape_cache_put(c, "k", None, 0.0)
    assert c == {}
