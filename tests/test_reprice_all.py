"""reprice_all: reprice every gated %-change horizon off a fresh spot price.

Closes the h6/h24 gap (the old RT-trigger loop only freshened h1/m5, leaving
the structure_edge/terminal_collapse/falling_day_flush/post_pump_corpse gates
reading stale long-horizon change)."""
from core.fast_watch import reprice_all, reprice_change_pct, REPRICE_HORIZONS


def test_includes_all_four_horizons():
    assert set(REPRICE_HORIZONS) == {"h1", "m5", "h6", "h24"}


def test_reprices_every_present_horizon():
    pc = {"h1": -20.0, "m5": -5.0, "h6": -30.0, "h24": -40.0}
    out = reprice_all(pc, snapshot_price=1.00, fresh_price=0.90)  # -10% fresh drop
    assert set(out) == {"h1", "m5", "h6", "h24"}
    # each equals the single-horizon repricer
    for h in pc:
        assert out[h] == reprice_change_pct(pc[h], 1.00, 0.90)


def test_skips_missing_horizons():
    out = reprice_all({"h1": -20.0}, 1.00, 0.90)
    assert set(out) == {"h1"}


def test_identity_when_fresh_equals_snap():
    pc = {"h1": -20.0, "h6": -30.0}
    out = reprice_all(pc, 1.00, 1.00)
    assert out["h1"] == -20.0 and out["h6"] == -30.0


def test_fresh_drop_deepens_the_dip():
    # snapshot says -20% at price 1.0; price has since fallen to 0.85.
    out = reprice_all({"h1": -20.0}, 1.00, 0.85)
    assert out["h1"] < -20.0  # fresh price is lower => deeper 1h drop


def test_bad_prices_yield_empty_not_raise():
    assert reprice_all({"h1": -20.0}, 0.0, 1.0) == {}
    assert reprice_all({"h1": -20.0}, 1.0, 0.0) == {}


def test_none_pricechange_is_empty():
    assert reprice_all(None, 1.0, 0.9) == {}


def test_custom_horizon_subset():
    out = reprice_all({"h1": -20.0, "h24": -40.0}, 1.0, 0.9, horizons=("h24",))
    assert set(out) == {"h24"}
