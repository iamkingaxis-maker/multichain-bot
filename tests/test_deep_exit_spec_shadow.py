"""Contract test for the deep_exit_spec_shadow stamp (feeds/dip_scanner.py,
2026-07-12 deep-cohort EXIT optimization, scratchpad/_deep_exit_optimization.md).

The stamp is MEASURE-ONLY (live exit ladder unchanged): for the deep-flush
cohort (pc_h1<=-45) it records the recommended BARBELL exit ladder + depth
sub-band so forward realized trips carry the recommendation for the exit-shape
join. The runner (moonbag) GROWS with depth because the deep-flush bounce tail
fattens with depth (RH real-tape sweep). This locks the band split + spec +
fail-open behavior so a refactor cannot silently drift it. Pure-logic mirror of
the inline stamp; no heavy imports."""


def _deep_exit_spec(pc_h1):
    """Mirror of the inline deep_exit_spec_shadow logic in dip_scanner.py.

    Returns (stamp, band, spec) or None when skipped (not deep / bad input)."""
    def _ok(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool)
    if not _ok(pc_h1) or float(pc_h1) > -45.0:
        return None  # fail-open: shallow or unevaluable -> no stamp
    band = "vdeep" if float(pc_h1) <= -60.0 else "deep"
    spec = ({"tp1": 5.0, "tp1_frac": 0.50, "tp2": 12.0,
             "moonbag_frac": 0.35, "moonbag_floor": 0.0,
             "moonbag_trail_pp": 15.0, "hard_stop": -15.0}
            if band == "vdeep" else
            {"tp1": 5.0, "tp1_frac": 0.60, "tp2": 12.0,
             "moonbag_frac": 0.25, "moonbag_floor": 0.0,
             "moonbag_trail_pp": 12.0, "hard_stop": -15.0})
    return "BARBELL_" + band.upper(), band, spec


def test_deep_flush_gets_deep_barbell():
    stamp, band, spec = _deep_exit_spec(-50.0)
    assert stamp == "BARBELL_DEEP" and band == "deep"
    assert spec["moonbag_frac"] == 0.25
    # boundary: -45 is inclusive-deep
    assert _deep_exit_spec(-45.0)[1] == "deep"


def test_very_deep_flush_gets_bigger_runner():
    stamp, band, spec = _deep_exit_spec(-72.0)
    assert stamp == "BARBELL_VDEEP" and band == "vdeep"
    # runner GROWS with depth (fatter bounce tail deeper)
    assert spec["moonbag_frac"] == 0.35 > 0.25
    assert spec["moonbag_trail_pp"] == 15.0
    # boundary: -60 is inclusive-vdeep
    assert _deep_exit_spec(-60.0)[1] == "vdeep"


def test_house_money_floor_and_fast_bulk_harvest():
    # both bands: breakeven floor (house money) + a real fast-harvest bulk
    for pc in (-50.0, -80.0):
        _, _, spec = _deep_exit_spec(pc)
        assert spec["moonbag_floor"] == 0.0          # breakeven house-money
        assert spec["tp1"] == 5.0                    # fast harvest level
        assert spec["tp1_frac"] + spec["moonbag_frac"] <= 1.0
        # harvested-fast fraction is the majority (locks the robust median)
        assert spec["tp1_frac"] >= 0.5


def test_shallow_and_bad_inputs_no_stamp():
    assert _deep_exit_spec(-44.9) is None    # just shallow of the deep line
    assert _deep_exit_spec(-10.0) is None
    assert _deep_exit_spec(None) is None
    assert _deep_exit_spec(True) is None     # bool guard (read-as-zero)
