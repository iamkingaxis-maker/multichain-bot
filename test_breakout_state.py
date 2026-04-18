from breakout.state import BreakoutState, BreakoutPosition


def test_new_state_empty():
    s = BreakoutState()
    assert s.watchlist == []
    assert s.open_positions == {}
    assert s.last_seen_close == {}
    assert s.scan_counters == {}


def test_set_watchlist_replaces():
    s = BreakoutState()
    s.set_watchlist(["BTCUSDT", "ETHUSDT"])
    assert s.watchlist == ["BTCUSDT", "ETHUSDT"]
    s.set_watchlist(["SOLUSDT"])
    assert s.watchlist == ["SOLUSDT"]


def test_add_and_remove_open_position():
    s = BreakoutState()
    pos = BreakoutPosition(
        symbol="BTCUSDT", entry_time="2026-04-17T12:00:00Z",
        entry_price=100.0, qty=5.0, cost_usd=500.0,
        score=8, resistance_level=99.5, tp_price=104.0, stop_price=97.0,
        entry_candle_volume=1234.0, peak_price=100.0,
    )
    s.open_positions[pos.symbol] = pos
    assert "BTCUSDT" in s.open_positions
    del s.open_positions["BTCUSDT"]
    assert "BTCUSDT" not in s.open_positions


def test_position_tp_hit_defaults_false():
    pos = BreakoutPosition(
        symbol="X", entry_time="t", entry_price=1.0, qty=1.0, cost_usd=1.0,
        score=7, resistance_level=1.0, tp_price=1.04, stop_price=0.97,
        entry_candle_volume=1.0, peak_price=1.0,
    )
    assert pos.tp_hit is False


def test_bump_scan_counter():
    s = BreakoutState()
    s.bump("gate_no_breakout")
    s.bump("gate_no_breakout")
    s.bump("gate_score_too_low")
    assert s.scan_counters["gate_no_breakout"] == 2
    assert s.scan_counters["gate_score_too_low"] == 1


def test_reset_scan_counters():
    s = BreakoutState()
    s.bump("x")
    s.reset_scan_counters()
    assert s.scan_counters == {}
