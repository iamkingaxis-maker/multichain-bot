import pytest
from feeds.candle_utils import Candle, ema, consecutive_reds_no_wick, rolling_avg_volume


def _c(o, h, l, c, v, t=0):
    return Candle(open_time=t, open=o, high=h, low=l, close=c, volume=v, close_time=t + 299)


def test_candle_fields_positive():
    k = _c(1.0, 1.2, 0.9, 1.1, 1000.0)
    assert k.open == 1.0 and k.close == 1.1 and k.volume == 1000.0


def test_ema_matches_reference():
    # EMA(3) of [1,2,3,4,5] with alpha=2/(N+1)=0.5, seeded on first value.
    # e1=1, e2=0.5*2+0.5*1=1.5, e3=0.5*3+0.5*1.5=2.25,
    # e4=0.5*4+0.5*2.25=3.125, e5=0.5*5+0.5*3.125=4.0625
    out = ema([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    assert abs(out - 4.0625) < 0.001


def test_ema_handles_short_series():
    # If series is shorter than period, return simple mean
    out = ema([2.0, 4.0], 5)
    assert abs(out - 3.0) < 1e-9


def test_consecutive_reds_no_wick_true():
    # 3 red candles where low == min(open,close) (no lower wick)
    reds = [_c(1.0, 1.0, 0.9, 0.9, 100.0),
            _c(0.9, 0.9, 0.8, 0.8, 100.0),
            _c(0.8, 0.8, 0.7, 0.7, 100.0)]
    assert consecutive_reds_no_wick(reds, 3) is True


def test_consecutive_reds_no_wick_false_when_wick_present():
    reds = [_c(1.0, 1.0, 0.85, 0.9, 100.0),  # lower wick
            _c(0.9, 0.9, 0.8, 0.8, 100.0),
            _c(0.8, 0.8, 0.7, 0.7, 100.0)]
    assert consecutive_reds_no_wick(reds, 3) is False


def test_consecutive_reds_no_wick_false_on_green():
    mixed = [_c(1.0, 1.1, 0.95, 1.05, 100.0),  # green
             _c(1.05, 1.05, 0.95, 0.95, 100.0),
             _c(0.95, 0.95, 0.85, 0.85, 100.0)]
    assert consecutive_reds_no_wick(mixed, 3) is False


def test_rolling_avg_volume():
    kl = [_c(1, 1, 1, 1, v) for v in [100, 200, 300, 400, 500]]
    assert abs(rolling_avg_volume(kl, 5) - 300.0) < 1e-9
    assert abs(rolling_avg_volume(kl, 3) - 400.0) < 1e-9  # last 3: 300,400,500
