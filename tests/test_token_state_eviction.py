"""RAM control (2026-06-28): _evict_stale_token_state TTL/LRU-evicts the
unbounded per-token state dicts in DipScanner (_exit_price_guard,
_slip_history, _addr_by_token) WITHOUT ever dropping an active token's state.

DipScanner's real constructor is heavy (network clients, bot wiring), so we
build a bare instance via object.__new__ and wire only the attributes the
helper touches. The helper is pure CPU over those dicts.
"""
import time
from collections import OrderedDict, deque

from feeds.dip_scanner import DipScanner


class _FakePos:
    def __init__(self, token, address, pair_address=None):
        self.token = token
        self.address = address
        self.pair_address = pair_address


class _FakePM:
    def __init__(self, positions):
        # token -> position object
        self._positions = positions


def _make_scanner():
    s = object.__new__(DipScanner)
    s._evict_interval_s = 300.0
    s._evict_ttl_s = 1800.0
    s._addr_by_token_max = 20000
    s._last_evict_ts = 0.0
    s._exit_price_guard = {}
    s._exit_price_guard_ts = {}
    s._slip_history = {}
    s._addr_by_token = OrderedDict()
    s.bot_position_managers = {}
    return s


def test_evicts_stale_exit_guard_keeps_fresh_and_active():
    s = _make_scanner()
    now = time.time()
    # active open position for ACTIVEADDR
    s.bot_position_managers = {
        "b1": _FakePM({"ACT": _FakePos("ACT", "activeaddr", "activepair")})
    }
    # stale guard entry (touched > ttl ago, no open position) -> evict
    s._exit_price_guard["staleaddr"] = {"last_good": 1.0, "pending": None}
    s._exit_price_guard_ts["staleaddr"] = now - 3600
    # fresh guard entry (touched recently) -> keep
    s._exit_price_guard["freshaddr"] = {"last_good": 2.0, "pending": None}
    s._exit_price_guard_ts["freshaddr"] = now - 10
    # active position's guard entry: ts is OLD (seeded at buy long ago) but it is
    # an open position -> protected -> keep
    s._exit_price_guard["activeaddr"] = {"last_good": 3.0, "pending": None}
    s._exit_price_guard_ts["activeaddr"] = now - 999999

    s._evict_stale_token_state()

    assert "staleaddr" not in s._exit_price_guard
    assert "staleaddr" not in s._exit_price_guard_ts
    assert "freshaddr" in s._exit_price_guard
    assert "activeaddr" in s._exit_price_guard, "active open position evicted!"


def test_evicts_stale_slip_history_keeps_active_addr():
    s = _make_scanner()
    now = time.time()
    s.bot_position_managers = {
        "b1": _FakePM({"ACT": _FakePos("ACT", "ActiveAddr")})
    }
    # stale: newest sample > ttl old
    dq_stale = deque(maxlen=10)
    dq_stale.append((now - 4000, 1.0, 1.0))
    s._slip_history["coldaddr"] = dq_stale
    # fresh: newest sample recent
    dq_fresh = deque(maxlen=10)
    dq_fresh.append((now - 60, 1.0, 1.0))
    s._slip_history["warmaddr"] = dq_fresh
    # active addr (open position) with OLD samples -> protected (case-insensitive)
    dq_active = deque(maxlen=10)
    dq_active.append((now - 999999, 1.0, 1.0))
    s._slip_history["activeaddr"] = dq_active

    s._evict_stale_token_state()

    assert "coldaddr" not in s._slip_history
    assert "warmaddr" in s._slip_history
    assert "activeaddr" in s._slip_history, "active addr slip history evicted!"


def test_addr_by_token_lru_cap_drops_oldest_keeps_active():
    s = _make_scanner()
    s._addr_by_token_max = 5
    # active open position token must survive even if it is the OLDEST entry
    s.bot_position_managers = {
        "b1": _FakePM({"OLDACTIVE": _FakePos("OLDACTIVE", "oa")})
    }
    # OLDACTIVE inserted first (oldest), then fill well past the cap
    s._addr_by_token["OLDACTIVE"] = "oa"
    for i in range(10):
        s._addr_by_token[f"T{i}"] = f"a{i}"

    s._evict_stale_token_state()

    assert len(s._addr_by_token) <= 5
    assert "OLDACTIVE" in s._addr_by_token, "active token dropped by LRU cap!"
    # most-recently-inserted tokens are retained
    assert "T9" in s._addr_by_token


def test_eviction_is_throttled():
    s = _make_scanner()
    s._exit_price_guard["staleaddr"] = {"last_good": 1.0, "pending": None}
    s._exit_price_guard_ts["staleaddr"] = time.time() - 99999
    # pretend an eviction just ran -> within the interval -> no-op this call
    s._last_evict_ts = time.time()
    s._evict_stale_token_state()
    assert "staleaddr" in s._exit_price_guard, "ran despite throttle window"


def test_eviction_fail_open_on_bad_state():
    s = _make_scanner()
    # Corrupt a structure the helper iterates; it must swallow and not raise.
    s._slip_history = None  # type: ignore
    s._last_evict_ts = 0.0
    s._evict_stale_token_state()  # should not raise


# --- MEM_REPORT (COMMIT 3) ---------------------------------------------------

def _make_scanner_for_mem():
    s = _make_scanner()
    s.trade_store = None
    s._h24_history = {}
    s._sticky_watchlist = {}
    s._fast_samples = {}
    s._scan_prefetch_cache = {}
    s._last_mem_report_ts = 0.0
    return s


def test_mem_report_noop_when_disabled(monkeypatch, caplog):
    monkeypatch.delenv("MEM_REPORT", raising=False)
    s = _make_scanner_for_mem()
    with caplog.at_level("INFO"):
        s._maybe_mem_report()
    assert not any("[MEM]" in r.message for r in caplog.records)


def test_mem_report_logs_when_enabled_and_throttles(monkeypatch, caplog):
    monkeypatch.setenv("MEM_REPORT", "1")
    s = _make_scanner_for_mem()
    s._exit_price_guard = {"a": {}, "b": {}}
    with caplog.at_level("INFO", logger="feeds.dip_scanner"):
        s._maybe_mem_report()
        first = [r for r in caplog.records if "[MEM]" in r.message]
        assert len(first) == 1, first
        assert "exit_guard=2" in first[0].message
        # Immediate second call is throttled (no new line).
        s._maybe_mem_report()
        assert len([r for r in caplog.records if "[MEM]" in r.message]) == 1
