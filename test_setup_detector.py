import pytest
from unittest.mock import MagicMock
from feeds.candle_utils import Candle
from feeds.setup_detector import SetupDetector, TriggerSignal, SetupPhase


def _c(o, h, l, c, v, t):
    return Candle(open_time=t, open=o, high=h, low=l, close=c, volume=v, close_time=t + 299)


def _make_cfg():
    cfg = MagicMock()
    cfg.scalp_impulse_min_pct = 10.0
    cfg.scalp_impulse_max_pct = 30.0
    cfg.scalp_impulse_lookback = 6
    cfg.scalp_pullback_min_pct = 30.0
    cfg.scalp_pullback_max_pct = 60.0
    cfg.scalp_sweep_vol_mult = 1.5
    cfg.scalp_sweep_vol_lookback = 20
    cfg.scalp_tp1_pct = 10.0
    cfg.scalp_stop_pct = 6.0
    cfg.scalp_min_rr = 2.0
    return cfg


def _impulse_series(base=1.0, start_t=1_000_000):
    """
    Build a sequence that walks through all four phases:
     - 20 candles of flat base (volume baseline)
     - 6-candle impulse +20%
     - 3-candle pullback -40% of the impulse range
     - 1 sweep candle wicking below the pullback low with 2x volume
     - 1 reclaim candle closing above the last pullback close
    """
    candles = []
    t = start_t
    # flat baseline — 20 bars
    for _ in range(20):
        candles.append(_c(base, base * 1.001, base * 0.999, base, 1000.0, t))
        t += 300
    # impulse — 6 bars, each +3% (≈+20% total), strong volume
    p = base
    for _ in range(6):
        new = p * 1.031
        candles.append(_c(p, new * 1.001, p * 0.999, new, 3000.0, t))
        p = new
        t += 300
    impulse_high = p
    impulse_low = base
    # pullback — 3 bars, retrace 40% of impulse
    retrace_target = impulse_high - (impulse_high - impulse_low) * 0.4
    step = (impulse_high - retrace_target) / 3
    for _ in range(3):
        new = p - step
        candles.append(_c(p, p * 1.001, new * 0.999, new, 1500.0, t))
        p = new
        t += 300
    pullback_low = p
    # sweep — wick below pullback low with vol spike
    sweep_low = pullback_low * 0.98
    sweep_close = pullback_low * 1.005
    candles.append(_c(pullback_low, pullback_low * 1.002, sweep_low, sweep_close, 4500.0, t))
    t += 300
    # reclaim — close above the last pullback close
    reclaim_close = pullback_low * 1.02
    candles.append(_c(sweep_close, reclaim_close * 1.001, sweep_close * 0.999,
                      reclaim_close, 3000.0, t))
    return candles, sweep_low, reclaim_close


def test_detector_fires_on_full_setup():
    cfg = _make_cfg()
    candles, sweep_low, reclaim_close = _impulse_series()
    det = SetupDetector(symbol="FOO", cfg=cfg)
    signal = det.evaluate(candles)
    assert isinstance(signal, TriggerSignal)
    assert signal.entry_price == pytest.approx(reclaim_close)
    # stop below sweep low (by 0.2%), capped at 6% below entry
    assert signal.stop_price <= sweep_low * 0.9985
    assert signal.stop_price >= reclaim_close * (1 - 0.06) - 1e-6
    # R/R ≥ 2
    assert signal.tp1_price == pytest.approx(reclaim_close * 1.10)
    rr = (signal.tp1_price - signal.entry_price) / (signal.entry_price - signal.stop_price)
    assert rr >= 2.0
    assert "impulse" in signal.reason.lower()


def test_detector_rejects_without_impulse():
    cfg = _make_cfg()
    # 30 flat candles — no impulse
    flat = [_c(1.0, 1.001, 0.999, 1.0, 1000.0, 1_000_000 + i * 300) for i in range(30)]
    det = SetupDetector(symbol="FOO", cfg=cfg)
    assert det.evaluate(flat) is None
    assert det.phase == SetupPhase.IDLE


def test_detector_rejects_without_sweep_volume():
    cfg = _make_cfg()
    candles, _, _ = _impulse_series()
    # Crush sweep volume below 1.5x avg
    sweep_idx = len(candles) - 2
    c = candles[sweep_idx]
    candles[sweep_idx] = _c(c.open, c.high, c.low, c.close, 500.0, c.open_time)
    det = SetupDetector(symbol="FOO", cfg=cfg)
    assert det.evaluate(candles) is None


def test_detector_rejects_without_reclaim():
    cfg = _make_cfg()
    candles, sweep_low, _ = _impulse_series()
    # Replace reclaim candle with one that closes BELOW sweep close (no reclaim)
    last = candles[-1]
    candles[-1] = _c(last.open, last.high, last.low * 0.99, last.open * 0.98,
                     last.volume, last.open_time)
    det = SetupDetector(symbol="FOO", cfg=cfg)
    assert det.evaluate(candles) is None


def test_detector_rejects_poor_rr():
    cfg = _make_cfg()
    cfg.scalp_min_rr = 10.0  # impossible
    candles, _, _ = _impulse_series()
    det = SetupDetector(symbol="FOO", cfg=cfg)
    assert det.evaluate(candles) is None


def test_detector_resets_after_fire():
    cfg = _make_cfg()
    candles, _, _ = _impulse_series()
    det = SetupDetector(symbol="FOO", cfg=cfg)
    sig = det.evaluate(candles)
    assert sig is not None
    # After firing, phase should reset so we don't fire on the same setup twice
    assert det.phase == SetupPhase.COOLDOWN
    sig2 = det.evaluate(candles)
    assert sig2 is None
