import pytest
from breakout.scoring import (
    ema,
    Kline,
    breakout_strength_score,
    is_bearish_engulfing,
    has_upper_wick_rejection,
    volume_drop,
)


def _k(o, h, l, c, v):
    return Kline(open_time=0, open=o, high=h, low=l, close=c, volume=v, close_time=0)


def test_kline_fields():
    k = Kline(
        open_time=1000, open=100.0, high=110.0, low=95.0,
        close=105.0, volume=2000.0, close_time=1900,
    )
    assert k.close == 105.0
    assert k.volume == 2000.0


def test_ema_single_value():
    assert ema([5.0], period=3) == 5.0


def test_ema_period_longer_than_data_falls_back_to_sma():
    assert ema([1.0, 2.0, 3.0], period=10) == pytest.approx(2.0)


def test_ema_known_values():
    prices = [1.0, 2.0, 3.0, 4.0, 5.0]
    # SMA-seed: (1+2+3)/3 = 2.0
    # step 4: 2.0 + 0.5*(4.0-2.0) = 3.0
    # step 5: 3.0 + 0.5*(5.0-3.0) = 4.0
    assert ema(prices, period=3) == pytest.approx(4.0)


def test_ema_flat_series_returns_that_value():
    assert ema([10.0] * 50, period=20) == pytest.approx(10.0)


def test_ema_empty_raises():
    with pytest.raises(ValueError):
        ema([], period=20)


# ── breakout_strength_score ─────────────────────────────────────────

def test_score_max_ten():
    candle = _k(100.0, 102.0, 99.8, 101.6, 2000.0)
    score, breakdown = breakout_strength_score(
        candle=candle,
        avg_volume_20=1000.0,
        resistance=101.0,
        ema50_1h=100.0,
        ema200_1h=98.0,
        consolidation_range=0.5,
    )
    assert score == 10
    assert breakdown["volume"] == 3
    assert breakdown["body"] == 2
    assert breakdown["breakout_size"] == 2
    assert breakdown["trend"] == 2
    assert breakdown["structure"] == 1


def test_score_zero_when_nothing_qualifies():
    candle = _k(100.0, 101.0, 99.0, 100.0, 500.0)
    score, _ = breakout_strength_score(
        candle=candle,
        avg_volume_20=1000.0,
        resistance=101.0,
        ema50_1h=105.0,
        ema200_1h=100.0,
        consolidation_range=5.0,
    )
    assert score == 0


def test_score_volume_tiers():
    base = dict(resistance=100.0, ema50_1h=100.0, ema200_1h=95.0, consolidation_range=10.0)
    c1 = _k(100.0, 100.5, 100.0, 100.2, 1000.0)
    s1, _ = breakout_strength_score(candle=c1, avg_volume_20=1000.0, **base)
    c2 = _k(100.0, 100.5, 100.0, 100.2, 1200.0)
    s2, _ = breakout_strength_score(candle=c2, avg_volume_20=1000.0, **base)
    c3 = _k(100.0, 100.5, 100.0, 100.2, 1500.0)
    s3, _ = breakout_strength_score(candle=c3, avg_volume_20=1000.0, **base)
    assert s1 < s2 < s3


def test_score_handles_zero_range_candle():
    candle = _k(100.0, 100.0, 100.0, 100.0, 1500.0)
    score, _ = breakout_strength_score(
        candle=candle, avg_volume_20=1000.0, resistance=100.0,
        ema50_1h=100.0, ema200_1h=95.0, consolidation_range=0.5,
    )
    assert isinstance(score, int)


# ── is_bearish_engulfing ─────────────────────────────────────────────

def test_bearish_engulfing_true():
    prev = _k(100.0, 102.0, 99.5, 101.5, 1000.0)
    curr = _k(101.8, 102.0, 98.0, 99.0, 1500.0)
    assert is_bearish_engulfing(prev, curr) is True


def test_bearish_engulfing_false_when_curr_green():
    prev = _k(100.0, 102.0, 99.5, 101.5, 1000.0)
    curr = _k(101.5, 103.0, 101.0, 102.5, 1500.0)
    assert is_bearish_engulfing(prev, curr) is False


def test_bearish_engulfing_false_when_prev_red():
    prev = _k(102.0, 102.5, 100.0, 100.5, 1000.0)
    curr = _k(100.5, 101.0, 99.0, 99.5, 1500.0)
    assert is_bearish_engulfing(prev, curr) is False


def test_bearish_engulfing_false_when_not_engulfed():
    prev = _k(100.0, 102.0, 99.5, 101.5, 1000.0)
    curr = _k(101.3, 101.5, 100.0, 100.2, 1500.0)
    assert is_bearish_engulfing(prev, curr) is False


# ── has_upper_wick_rejection ─────────────────────────────────────────

def test_upper_wick_rejection_detected():
    candle = _k(100.0, 102.0, 99.9, 100.2, 1000.0)
    assert has_upper_wick_rejection(candle, threshold=0.6) is True


def test_upper_wick_rejection_not_detected():
    candle = _k(100.0, 100.3, 99.5, 100.2, 1000.0)
    assert has_upper_wick_rejection(candle, threshold=0.6) is False


def test_upper_wick_rejection_zero_range_returns_false():
    candle = _k(100.0, 100.0, 100.0, 100.0, 1000.0)
    assert has_upper_wick_rejection(candle) is False


# ── volume_drop ──────────────────────────────────────────────────────

def test_volume_drop_detected():
    assert volume_drop(current_vol=400.0, baseline_vol=1000.0, threshold=0.5) is True


def test_volume_drop_not_detected():
    assert volume_drop(current_vol=800.0, baseline_vol=1000.0, threshold=0.5) is False


def test_volume_drop_zero_baseline_returns_false():
    assert volume_drop(current_vol=0.0, baseline_vol=0.0) is False
