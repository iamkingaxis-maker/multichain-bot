import core.slippage_model as sm

CURVE = {  # sampled Jupiter impact curve (per-token, real)
    "slip_buy_500_pct": 0.65,
    "slip_buy_2000_pct": 1.41,
    "slip_buy_5000_pct": 2.96,
}


def test_impact_interpolates_sampled_points():
    assert sm.impact_pct_for_size(500, CURVE) == 0.65
    assert sm.impact_pct_for_size(2000, CURVE) == 1.41


def test_small_order_near_zero_impact():
    # Jupiter-confirmed: a $20 order has ~0 price impact
    assert sm.impact_pct_for_size(20, CURVE) < 0.05


def test_missing_curve_uses_low_default():
    assert sm.impact_pct_for_size(2000, {}) == sm.DEFAULT_IMPACT_PCT
    assert sm.DEFAULT_IMPACT_PCT <= 0.15  # NOT the old 0.70 over-estimate


def test_fixed_fee_dominates_small_trades():
    # $0.10 fee on $20 = 0.5% per side; impact ~0 → per-side ≈ 0.5%
    ps20 = sm.per_side_cost_pct(20, sm.impact_pct_for_size(20, CURVE))
    assert abs(ps20 - 0.5) < 0.1


def test_fixed_fee_component_shrinks_with_size():
    # the FIXED-$ fee becomes a negligible % on big trades — the whole point
    fee_pct_20 = sm.FEE_USD_PER_TX / 20 * 100    # ~0.5%
    fee_pct_160 = sm.FEE_USD_PER_TX / 160 * 100  # ~0.0625%
    assert fee_pct_160 < 0.10 < fee_pct_20


def test_bigger_trades_are_cheaper_per_dollar():
    cheap = sm.per_side_cost_pct(160, sm.impact_pct_for_size(160, CURVE))
    dear = sm.per_side_cost_pct(20, sm.impact_pct_for_size(20, CURVE))
    assert cheap < dear  # fixed-fee structure → size-up wins


def test_buy_marks_up_sell_marks_down():
    px, impact = sm.buy_fill_price(1.0, 20, CURVE)
    assert px > 1.0
    sell = sm.sell_fill_price(1.0, 20, impact)
    assert sell < 1.0


def test_roundtrip_cost_small_trade_about_1pct():
    entry, impact = sm.buy_fill_price(1.0, 20, CURVE)
    exit_px = sm.sell_fill_price(1.0, 20, impact)
    rt = (1 - exit_px / entry) * 100
    assert 0.8 < rt < 1.3  # ~1% round trip on $20 (fee-dominated)


def test_roundtrip_cost_big_trade_much_smaller():
    entry, impact = sm.buy_fill_price(1.0, 160, CURVE)
    exit_px = sm.sell_fill_price(1.0, 160, impact)
    rt = (1 - exit_px / entry) * 100
    assert rt < 0.6  # big trade amortizes the fixed fee


def test_disabled_returns_mid(monkeypatch):
    monkeypatch.setattr(sm, "SLIPPAGE_ENABLED", False)
    px, impact = sm.buy_fill_price(1.0, 20, CURVE)
    assert px == 1.0 and impact == 0.0
    assert sm.sell_fill_price(1.0, 20, 0.5) == 1.0
