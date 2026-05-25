import importlib
import core.slippage_model as sm


CURVE = {
    "slip_buy_500_pct": 0.65,
    "slip_buy_2000_pct": 1.41,
    "slip_buy_5000_pct": 2.96,
}


def test_slip_interpolates_sampled_points():
    assert sm.slip_pct_for_size(500, CURVE) == 0.65
    assert sm.slip_pct_for_size(2000, CURVE) == 1.41
    assert sm.slip_pct_for_size(5000, CURVE) == 2.96


def test_small_order_approaches_zero_slip():
    # $20 is tiny → interpolated from (0,0)..(500,0.65) ≈ 0.026% (fee dominates)
    s = sm.slip_pct_for_size(20, CURVE)
    assert 0.0 <= s < 0.1


def test_midpoint_interpolates_linearly():
    # halfway between $500 (0.65) and $2000 (1.41) is $1250 → ~1.03
    s = sm.slip_pct_for_size(1250, CURVE)
    assert abs(s - 1.03) < 0.05


def test_above_top_sample_extrapolates_up():
    # $8000 extrapolates beyond $5000 along the last segment's slope
    s = sm.slip_pct_for_size(8000, CURVE)
    assert s > 2.96


def test_missing_curve_uses_default():
    assert sm.slip_pct_for_size(2000, {}) == sm.DEFAULT_SLIP_PCT
    assert sm.slip_pct_for_size(2000, None) == sm.DEFAULT_SLIP_PCT


def test_sell_curve_falls_back_to_buy_curve():
    # no slip_sell_* keys → reuse buy curve
    assert sm.slip_pct_for_size(2000, CURVE, side="sell") == 1.41


def test_buy_fill_marks_price_up():
    px, slip = sm.buy_fill_price(1.0, 2000, CURVE)
    # 1.0 * (1 + (1.41 + 0.30)/100) = 1.0171
    assert abs(px - 1.0171) < 1e-4
    assert slip == 1.41


def test_sell_fill_marks_price_down():
    px = sm.sell_fill_price(1.0, 1.41)
    # 1.0 * (1 - (1.41 + 0.30)/100) = 0.9829
    assert abs(px - 0.9829) < 1e-4


def test_roundtrip_cost_is_realistic():
    # $2000 round trip at flat price: buy up + sell down = ~2*(1.41+0.30)% loss
    entry, slip = sm.buy_fill_price(1.0, 2000, CURVE)
    exit_px = sm.sell_fill_price(1.0, slip)
    pnl_pct = (exit_px / entry - 1) * 100
    assert -3.6 < pnl_pct < -3.2  # ≈ -3.4% round-trip drag


def test_disabled_returns_mid(monkeypatch):
    monkeypatch.setattr(sm, "SLIPPAGE_ENABLED", False)
    px, slip = sm.buy_fill_price(1.0, 5000, CURVE)
    assert px == 1.0 and slip == 0.0
    assert sm.sell_fill_price(1.0, 2.96) == 1.0
