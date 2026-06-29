from feeds.dexscreener_chart_format import (
    rolling_high_from_bars,
    rolling_low_from_bars,
)


def _bar(ts_ms, high, low):
    return {"ts_ms": ts_ms, "open": high, "close": low, "high": high, "low": low,
            "volume_usd": 0.0, "block_first": 0, "block_last": 0}


def test_high_low_over_window():
    now_ms = 2_000_000_000_000
    bars = [
        _bar(now_ms - 100_000, 5.0, 4.0),
        _bar(now_ms - 50_000, 3.0, 2.0),
        _bar(now_ms - 10_000, 4.0, 3.5),
    ]
    # 200s window includes all -> high 5.0, low 2.0
    assert rolling_high_from_bars(bars, 200.0, now_ms) == 5.0
    assert rolling_low_from_bars(bars, 200.0, now_ms) == 2.0


def test_window_excludes_old_bars():
    now_ms = 2_000_000_000_000
    bars = [
        _bar(now_ms - 100_000, 9.0, 8.0),  # 100s old
        _bar(now_ms - 10_000, 3.0, 2.0),
    ]
    # 30s window excludes the 100s-old bar
    assert rolling_high_from_bars(bars, 30.0, now_ms) == 3.0
    assert rolling_low_from_bars(bars, 30.0, now_ms) == 2.0


def test_empty_bars_returns_none():
    assert rolling_high_from_bars([], 100.0, 2_000_000_000_000) is None
    assert rolling_low_from_bars([], 100.0, 2_000_000_000_000) is None


def test_malformed_bars_skipped():
    now_ms = 2_000_000_000_000
    bars = [
        {"ts_ms": now_ms - 5_000},                 # missing high/low
        {"high": "x", "low": "y", "ts_ms": now_ms},  # non-numeric
        _bar(now_ms - 1_000, 7.0, 6.0),
    ]
    assert rolling_high_from_bars(bars, 100.0, now_ms) == 7.0
    assert rolling_low_from_bars(bars, 100.0, now_ms) == 6.0


def test_nonpositive_high_ignored():
    now_ms = 2_000_000_000_000
    bars = [_bar(now_ms - 1_000, 0.0, 0.0), _bar(now_ms, 2.0, 1.0)]
    assert rolling_high_from_bars(bars, 100.0, now_ms) == 2.0
