from core.realtime_dip import RollingPriceWindow
from core.realtime_dip import compute_rt_price_change, HORIZON_SECS, RollingPriceWindow


def _bar(ts_ms, high, low):
    return {"ts_ms": ts_ms, "open": high, "close": low, "high": high, "low": low,
            "volume_usd": 0.0, "block_first": 0, "block_last": 0}


def test_compute_dip_off_buffer_only():
    now = 2_000_000_000.0  # seconds
    w = RollingPriceWindow()
    # oldest sample at now-1800 makes the buffer span both m5 (>=150s) and
    # h1 (>=1800s) windows; the 2.0 high sits inside both horizon windows.
    w.append(now - 1800, 1.0)  # old span anchor (also inside h1 window)
    w.append(now - 200, 2.0)   # recent high (inside m5 window)
    w.append(now - 1, 1.0)
    pc, cov = compute_rt_price_change(w, [], fresh_price=1.0, now=now)
    assert cov == "BUFFER_ONLY"
    # m5/h1 windows both see high 2.0 -> -50%
    assert pc["m5"] == -50.0
    assert pc["h1"] == -50.0
    # h6/h24 require ~10800s/43200s of buffer span -> never emitted buffer-only here
    assert "h6" not in pc
    assert "h24" not in pc


def test_compute_combines_bars_and_buffer():
    now = 2_000_000_000.0
    now_ms = now * 1000.0
    w = RollingPriceWindow()
    w.append(now - 200, 1.0)                      # >=2 samples; spans m5 (>=150s)
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


def test_scale_guard_rejects_offscale_bars():
    # io.dx bars come back 30x off-scale (microcap unit bug): newest bar close
    # is 30x the fresh price -> ratio 0.033 < 0.1 -> reject bars, fall back to
    # buffer-only so a real dip is NOT overwritten with a fake pump.
    now = 2_000_000_000.0
    now_ms = now * 1000.0
    w = RollingPriceWindow()
    w.append(now - 200, 1.0)   # buffer in correct (fresh) scale
    w.append(now - 1, 1.0)
    # newest bar close=30.0 (30x fresh=1.0); high also off-scale
    bars = [_bar(now_ms - 1800_000, 120.0, 90.0), _bar(now_ms - 1000, 33.0, 30.0)]
    pc, cov = compute_rt_price_change(w, bars, fresh_price=1.0, now=now)
    assert cov == "BUFFER_ONLY"      # bars rejected by scale guard
    assert "h1" not in pc or pc.get("h1") == 0.0  # no garbage +pump from bars


def test_scale_guard_keeps_inscale_bars():
    # newest bar close ~= fresh price (ratio ~1) -> bars accepted normally.
    now = 2_000_000_000.0
    now_ms = now * 1000.0
    w = RollingPriceWindow()
    w.append(now - 200, 1.0)
    w.append(now - 1, 1.0)
    bars = [_bar(now_ms - 1800_000, 4.0, 1.0)]  # close=low=1.0 ~= fresh 1.0
    pc, cov = compute_rt_price_change(w, bars, fresh_price=1.0, now=now)
    assert cov == "BARS+BUFFER"
    assert pc["h1"] == -75.0  # bar high 4.0 used -> real -75% dip preserved


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


def test_oldest_ts():
    w = RollingPriceWindow()
    assert w.oldest_ts() is None
    w.append(1000.0, 1.0)
    w.append(1005.0, 2.0)
    assert w.oldest_ts() == 1000.0
    assert w.newest_ts() == 1005.0


def test_buffer_only_short_span_skips_m5():
    # Buffer spans only ~120s: m5 needs >=0.5*300=150s span -> SKIPPED.
    now = 2_000_000_000.0
    w = RollingPriceWindow()
    w.append(now - 120, 2.0)
    w.append(now - 1, 1.0)
    pc, cov = compute_rt_price_change(w, [], fresh_price=1.0, now=now)
    assert "m5" not in pc
    # Nothing buffer-only spans h1/h6/h24 either -> overall NONE.
    assert "h1" not in pc and "h6" not in pc and "h24" not in pc
    assert cov == "NONE"
    assert pc == {}


def test_buffer_only_spanning_m5_emits_m5_not_long():
    # Buffer spans ~200s: m5 (>=150s) emitted; h6/h24 never buffer-only.
    now = 2_000_000_000.0
    w = RollingPriceWindow()
    w.append(now - 200, 2.0)   # high inside m5 window, span 200>=150
    w.append(now - 1, 1.0)
    pc, cov = compute_rt_price_change(w, [], fresh_price=1.0, now=now)
    assert cov == "BUFFER_ONLY"
    assert pc["m5"] == -50.0
    assert "h6" not in pc
    assert "h24" not in pc


def test_bars_unlock_long_horizon_with_shallow_buffer():
    # Shallow buffer (~120s span) but a real bar inside the h6 window -> h6 emitted.
    now = 2_000_000_000.0
    now_ms = now * 1000.0
    w = RollingPriceWindow()
    w.append(now - 120, 1.0)
    w.append(now - 1, 1.0)
    bars = [_bar(now_ms - 7200_000, 4.0, 3.0)]  # 2h-old bar high 4.0 (inside h6 window)
    pc, cov = compute_rt_price_change(w, bars, fresh_price=1.0, now=now)
    assert cov == "BARS+BUFFER"
    assert pc["h6"] == -75.0
    assert pc["h24"] == -75.0
    # m5/h1: bar is 2h old (outside both), buffer span 120s < 150s -> skipped.
    assert "m5" not in pc
    assert "h1" not in pc
