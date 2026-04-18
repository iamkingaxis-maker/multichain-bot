import pytest
from breakout.scoring import ema, Kline


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
