"""Contract test for the aged_pond_absorb_shadow stamp (feeds/dip_scanner.py,
2026-07-13 aged-pond mine, scratchpad/_sol_aged_pond_mine.md).

The stamp favors an AGED pool (lifecycle_age_hours in [6, 24)) with STRONG
live buy-side absorption (net_flow_15s_imbalance >= 0.4). This is the
best-of-mine 6-24h absorb cohort: ex-top2 token-median +2.7 (n=22, 64%
tok-green, 3/4 OOS halves green) vs the pond base -2.5. It TIGHTENS the
adolescent_absorb bot's own net_flow_15s_imbalance>=0 gate. The stamp also
records a post-pump flag (pc_h6<0) for the forward join but does NOT gate on
it (pc_h6<0 is ~86% of the pond and not the discriminating lever). Pure-logic
mirror of the inline stamp; no heavy imports."""


def _aged_pond_stamp(age_h, nf15_imbal, pc_h6=None):
    """Mirror of the inline aged_pond_absorb_shadow logic in dip_scanner.py.

    Returns (stamp, postpump_flag). stamp is None when the required inputs
    (age, nf15) are missing/invalid -> fail-open, no favoring."""
    def _ok(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool)
    if not (_ok(age_h) and _ok(nf15_imbal)):
        return None, None  # fail-open
    aged = (6.0 <= float(age_h) < 24.0)
    postpump = _ok(pc_h6) and float(pc_h6) < 0.0
    stamp = "FAVOR" if (aged and float(nf15_imbal) >= 0.4) else "SKIP"
    return stamp, bool(postpump)


def test_aged_pool_with_strong_absorption_is_favored():
    # 6-24h AND nf15 imbalance >= 0.4 -> the +2.7 ex2 green cohort
    assert _aged_pond_stamp(9.3, 0.5)[0] == "FAVOR"
    assert _aged_pond_stamp(6.0, 0.4)[0] == "FAVOR"    # both boundaries inclusive
    assert _aged_pond_stamp(23.99, 0.62)[0] == "FAVOR"


def test_young_pool_is_skipped_even_with_absorption():
    # <6h is the worst absorb band (ex2 -5.1) regardless of flow -> not favored.
    assert _aged_pond_stamp(3.0, 0.9)[0] == "SKIP"
    assert _aged_pond_stamp(5.99, 0.5)[0] == "SKIP"    # just under the aged line


def test_over_aged_pool_is_skipped():
    # >=24h leaves the mined 6-24h pond (24-96h underpowered n=2) -> not favored.
    assert _aged_pond_stamp(24.0, 0.8)[0] == "SKIP"
    assert _aged_pond_stamp(48.0, 0.5)[0] == "SKIP"


def test_aged_but_weak_absorption_is_skipped():
    # aged pool but neutral/weak flow -> not favored. This is the lever: the
    # aged pond only pays when buyers are EATING the dip now (nf15>=0.4), not
    # merely neutral (>=0, the bot's base gate). pond base is -2.5.
    assert _aged_pond_stamp(12.0, 0.39)[0] == "SKIP"
    assert _aged_pond_stamp(12.0, 0.0)[0] == "SKIP"
    assert _aged_pond_stamp(12.0, -0.2)[0] == "SKIP"


def test_postpump_flag_recorded_not_gated():
    # pc_h6<0 is recorded for the forward join but does not change FAVOR/SKIP.
    fav_pp, pp_true = _aged_pond_stamp(10.0, 0.5, pc_h6=-48.0)
    fav_mid, pp_false = _aged_pond_stamp(10.0, 0.5, pc_h6=205.0)
    assert fav_pp == "FAVOR" and pp_true is True
    assert fav_mid == "FAVOR" and pp_false is False   # still FAVOR, flag flips only
    # missing pc_h6 -> postpump False, stamp unaffected
    assert _aged_pond_stamp(10.0, 0.5) == ("FAVOR", False)


def test_fail_open_on_missing_or_bad_inputs():
    # missing either required axis -> no stamp (fail-open, never favor on unknown)
    assert _aged_pond_stamp(None, 0.5) == (None, None)
    assert _aged_pond_stamp(10.0, None) == (None, None)
    # bool is not valid numeric (read-as-zero / isinstance guard)
    assert _aged_pond_stamp(True, 0.5) == (None, None)
    assert _aged_pond_stamp(10.0, False) == (None, None)
