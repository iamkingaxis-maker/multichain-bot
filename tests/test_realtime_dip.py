from core.realtime_dip import RollingPriceWindow
from core.realtime_dip import compute_rt_price_change, HORIZON_SECS, RollingPriceWindow


def _bar(ts_ms, high, low):
    return {"ts_ms": ts_ms, "open": high, "close": low, "high": high, "low": low,
            "volume_usd": 0.0, "block_first": 0, "block_last": 0}


def test_compute_dip_off_buffer_only():
    now = 2_000_000_000.0  # seconds
    w = RollingPriceWindow()
    w.append(now - 60, 2.0)   # recent high
    w.append(now - 1, 1.0)
    pc, cov = compute_rt_price_change(w, [], fresh_price=1.0, now=now)
    assert cov == "BUFFER_ONLY"
    # m5/h1 windows both see high 2.0 -> -50%
    assert pc["m5"] == -50.0
    assert pc["h1"] == -50.0


def test_compute_combines_bars_and_buffer():
    now = 2_000_000_000.0
    now_ms = now * 1000.0
    w = RollingPriceWindow()
    w.append(now - 120, 1.0)                      # >=2 samples so the buffer is usable
    w.append(now - 1, 1.0)
    bars = [_bar(now_ms - 1800_000, 4.0, 3.0)]   # 30min-old bar high 4.0 (in h1 window)
    pc, cov = compute_rt_price_change(w, bars, fresh_price=1.0, now=now)
    assert cov == "BARS+BUFFER"
    # h1 sees the bar high 4.0 -> -75%; m5 sees only the buffer 1.0 -> 0%
    assert pc["h1"] == -75.0
    assert pc["m5"] == 0.0


def test_compute_none_when_empty():
    now = 2_000_000_000.0
    w = RollingPriceWindow()
    pc, cov = compute_rt_price_change(w, [], fresh_price=1.0, now=now)
    assert cov == "NONE"
    assert pc == {}


def test_compute_none_on_nonpositive_fresh():
    now = 2_000_000_000.0
    w = RollingPriceWindow()
    w.append(now - 1, 2.0)
    pc, cov = compute_rt_price_change(w, [], fresh_price=0.0, now=now)
    assert cov == "NONE"
    assert pc == {}


def test_compute_none_when_buffer_stale_and_no_bars():
    now = 2_000_000_000.0
    w = RollingPriceWindow()
    w.append(now - 600, 2.0)   # newest sample 600s old > max_age 90s
    pc, cov = compute_rt_price_change(w, [], fresh_price=1.0, now=now, max_age_secs=90.0)
    assert cov == "NONE"
    assert pc == {}


def test_compute_uses_bars_when_buffer_stale():
    now = 2_000_000_000.0
    now_ms = now * 1000.0
    w = RollingPriceWindow()
    w.append(now - 600, 2.0)   # stale buffer
    bars = [_bar(now_ms - 30_000, 4.0, 3.0)]  # fresh bar 30s old
    pc, cov = compute_rt_price_change(w, bars, fresh_price=1.0, now=now, max_age_secs=90.0)
    assert cov == "BARS+BUFFER"
    assert pc["m5"] == -75.0


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
