from breakout.regime import compute_btc_regime
from breakout.scoring import Kline


def _uptrend(n=210, start=80.0, end=100.0):
    step = (end - start) / (n - 1)
    return [Kline(0, start + i * step, start + i * step + 0.2, start + i * step - 0.2,
                  start + i * step, 1000.0, 0) for i in range(n)]


def _downtrend(n=210, start=100.0, end=90.0):
    step = (end - start) / (n - 1)
    return [Kline(0, start + i * step, start + i * step + 0.2, start + i * step - 0.2,
                  start + i * step, 1000.0, 0) for i in range(n)]


def _flat_15m(close=100.0):
    return [Kline(0, close, close + 0.1, close - 0.1, close, 1000.0, 0) for _ in range(2)]


def test_regime_green_when_btc_above_ema_and_1h_rising():
    k1h = _uptrend()
    k15 = _flat_15m(close=k1h[-1].close)
    r = compute_btc_regime(k1h, k15, risk_off_drop_pct=2.0, red_1h_pct=-1.0)
    assert r.label == "green"
    assert r.btc_close > r.btc_ema50_1h
    assert r.btc_1h_pct > 0


def test_regime_red_when_btc_below_ema():
    k1h = _downtrend()
    k15 = _flat_15m(close=k1h[-1].close)
    r = compute_btc_regime(k1h, k15, risk_off_drop_pct=2.0, red_1h_pct=-1.0)
    assert r.label == "red"
    assert r.btc_close < r.btc_ema50_1h


def test_regime_red_when_btc_1h_pct_below_threshold():
    k1h = _uptrend()
    # Force the last 1h candle to drop -1.5%
    last = k1h[-1]
    prev = k1h[-2]
    k1h[-1] = Kline(last.open_time, last.open, last.high, last.low,
                    prev.close * 0.985, last.volume, last.close_time)
    k15 = _flat_15m(close=k1h[-1].close)
    r = compute_btc_regime(k1h, k15, risk_off_drop_pct=2.0, red_1h_pct=-1.0)
    assert r.label == "red"
    assert r.btc_1h_pct < -1.0


def test_regime_risk_off_when_15m_drops_more_than_threshold():
    k1h = _uptrend()
    last_close = k1h[-1].close
    # 15m candle: open high, close -2.5% lower
    k15 = [
        Kline(0, last_close, last_close + 0.1, last_close - 0.1, last_close, 1000.0, 0),
        Kline(0, last_close, last_close, last_close * 0.975, last_close * 0.975, 1000.0, 0),
    ]
    r = compute_btc_regime(k1h, k15, risk_off_drop_pct=2.0, red_1h_pct=-1.0)
    assert r.label == "risk_off"
    assert r.btc_15m_drop_pct <= -2.0


def test_regime_risk_off_precedes_red_check():
    # BTC is ALSO below EMA (would be red) — but 15m drop of 2.5% → risk_off wins
    k1h = _downtrend()
    last_close = k1h[-1].close
    k15 = [
        Kline(0, last_close, last_close + 0.1, last_close - 0.1, last_close, 1000.0, 0),
        Kline(0, last_close, last_close, last_close * 0.97, last_close * 0.97, 1000.0, 0),
    ]
    r = compute_btc_regime(k1h, k15, risk_off_drop_pct=2.0, red_1h_pct=-1.0)
    assert r.label == "risk_off"


def test_regime_custom_thresholds():
    k1h = _uptrend()
    # Force -0.5% 1h change (between default -1.0 and a stricter -0.3)
    last = k1h[-1]
    prev = k1h[-2]
    k1h[-1] = Kline(last.open_time, last.open, last.high, last.low,
                    prev.close * 0.995, last.volume, last.close_time)
    k15 = _flat_15m(close=k1h[-1].close)
    # With default -1.0 threshold: green (only -0.5 > -1.0)
    r1 = compute_btc_regime(k1h, k15, risk_off_drop_pct=2.0, red_1h_pct=-1.0)
    assert r1.label == "green"
    # With stricter -0.3 threshold: red
    r2 = compute_btc_regime(k1h, k15, risk_off_drop_pct=2.0, red_1h_pct=-0.3)
    assert r2.label == "red"
