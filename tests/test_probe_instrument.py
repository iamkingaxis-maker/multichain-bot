"""Pure live-fill instrumentation helpers (piece 3/4)."""
from core.probe_instrument import fill_slippage_pct, entry_vs_local_low_pct, fill_metrics


def test_buy_slippage_adverse_positive():
    # paid 1.01 vs mid 1.00 -> +1.0% adverse
    assert fill_slippage_pct(1.00, 1.01, "buy") == 1.0
    # got a better buy (0.99) -> negative (favorable)
    assert fill_slippage_pct(1.00, 0.99, "buy") == -1.0


def test_sell_slippage_adverse_positive():
    # received 0.99 vs mid 1.00 -> +1.0% adverse (got less)
    assert fill_slippage_pct(1.00, 0.99, "sell") == 1.0
    assert fill_slippage_pct(1.00, 1.01, "sell") == -1.0   # got more = favorable


def test_slippage_bad_inputs_none():
    assert fill_slippage_pct(0, 1, "buy") is None
    assert fill_slippage_pct(1, 0, "buy") is None
    assert fill_slippage_pct(None, 1, "buy") is None
    assert fill_slippage_pct("x", 1, "buy") is None


def test_entry_vs_local_low():
    assert entry_vs_local_low_pct(1.05, 1.00) == 5.0   # filled 5% above the dip-low
    assert entry_vs_local_low_pct(1.00, 1.00) == 0.0
    assert entry_vs_local_low_pct(None, 1.0) is None


def test_fill_metrics_buy_includes_entry_gap():
    m = fill_metrics("buy", mid=1.00, fill=1.012, route="metis", latency_ms=420,
                     ultra_slippage_pct=0.8, entry_price=1.012, local_low=0.99)
    assert m["live_slippage_pct"] == 1.2 and m["live_route"] == "metis"
    assert m["live_latency_ms"] == 420 and m["live_ultra_slippage_pct"] == 0.8
    assert m["live_entry_vs_local_low_pct"] == round((1.012-0.99)/0.99*100, 4)


def test_fill_metrics_sell_omits_entry_gap():
    m = fill_metrics("sell", mid=1.00, fill=0.99)
    assert "live_entry_vs_local_low_pct" not in m
    assert m["live_slippage_pct"] == 1.0
