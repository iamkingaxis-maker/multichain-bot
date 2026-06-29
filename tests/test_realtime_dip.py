from core.realtime_dip import RollingPriceWindow


def test_append_and_window_high_low():
    w = RollingPriceWindow()
    w.append(1000.0, 1.0)
    w.append(1001.0, 2.0)
    w.append(1002.0, 1.5)
    assert w.window_high(10.0, 1002.0) == 2.0
    assert w.window_low(10.0, 1002.0) == 1.0
    assert len(w) == 3
    assert w.newest_ts() == 1002.0


def test_window_excludes_out_of_window_samples():
    w = RollingPriceWindow()
    w.append(1000.0, 5.0)   # old high
    w.append(1100.0, 2.0)
    # window of 50s ending at 1100 excludes the 1000 sample
    assert w.window_high(50.0, 1100.0) == 2.0
    # wide window includes it
    assert w.window_high(500.0, 1100.0) == 5.0


def test_evicts_by_age_on_append():
    w = RollingPriceWindow(max_age_secs=100.0)
    w.append(1000.0, 1.0)
    w.append(1200.0, 2.0)   # now=1200, max_age=100 -> 1000 evicted
    assert len(w) == 1
    assert w.window_high(10_000.0, 1200.0) == 2.0


def test_evicts_by_count():
    w = RollingPriceWindow(max_age_secs=1e9, max_samples=3)
    for i in range(5):
        w.append(1000.0 + i, float(i + 1))
    assert len(w) == 3
    # newest three are prices 3,4,5
    assert w.window_high(10_000.0, 1004.0) == 5.0
    assert w.window_low(10_000.0, 1004.0) == 3.0


def test_ignores_nonpositive_prices():
    w = RollingPriceWindow()
    w.append(1000.0, 0.0)
    w.append(1001.0, -1.0)
    assert len(w) == 0
    assert w.window_high(10.0, 1001.0) is None
    assert w.newest_ts() is None


def test_empty_window_returns_none():
    w = RollingPriceWindow()
    assert w.window_high(10.0, 1000.0) is None
    assert w.window_low(10.0, 1000.0) is None
    assert len(w) == 0
