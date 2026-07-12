"""Contract test for the deep_combo_shadow stamp (feeds/dip_scanner.py,
2026-07-12 young-lane selection combo hunt, scratchpad/_sol_deep_gate.md).

The stamp favors DEEP 1h capitulation (pc_h1<=-45) AND a liquidity floor
(liquidity_usd>=30k) — a genuine interaction that turns the deep-alone
less-red cohort (-3.0 ex2 token-median) GREEN (+4.6). This locks the
threshold contract + fail-open behavior so a refactor cannot silently
drift the gate. Pure-logic mirror of the inline stamp; no heavy imports."""


def _deep_combo_stamp(pc_h1, liquidity_usd):
    """Mirror of the inline deep_combo_shadow logic in dip_scanner.py.

    Returns the stamp string, or None when the stamp is skipped entirely
    (missing/invalid inputs -> fail-open, no favoring)."""
    def _ok(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool)
    if not (_ok(pc_h1) and _ok(liquidity_usd)):
        return None  # fail-open: cannot evaluate -> no stamp, not favored
    return "FAVOR" if (float(pc_h1) <= -45.0
                       and float(liquidity_usd) >= 30000.0) else "SKIP"


def test_deep_flush_on_liquid_pool_is_favored():
    # deep 1h flush AND liquid pool -> the GREEN cohort
    assert _deep_combo_stamp(-52.0, 34000.0) == "FAVOR"
    assert _deep_combo_stamp(-45.0, 30000.0) == "FAVOR"  # boundary inclusive


def test_deep_but_thin_pool_is_skipped():
    # deep flush but BELOW the liquidity floor -> not favored (thin knives
    # bleed: SHALLOW/thin cohort was the worst at -6.3)
    assert _deep_combo_stamp(-60.0, 29999.0) == "SKIP"
    assert _deep_combo_stamp(-60.0, 12000.0) == "SKIP"


def test_liquid_but_shallow_is_skipped():
    # liquid pool but NOT a deep flush -> not favored. This is the
    # interaction guard: liq>=30k ALONE is red (-5.0); the floor only pays
    # on the deep-flush side.
    assert _deep_combo_stamp(-20.0, 80000.0) == "SKIP"
    assert _deep_combo_stamp(-44.9, 100000.0) == "SKIP"  # just above the deep line


def test_fail_open_on_missing_or_bad_inputs():
    # missing either axis -> no stamp (fail-open, never favor on unknown)
    assert _deep_combo_stamp(None, 40000.0) is None
    assert _deep_combo_stamp(-50.0, None) is None
    # bool is not a valid numeric (read-as-zero / isinstance guard)
    assert _deep_combo_stamp(True, 40000.0) is None
    assert _deep_combo_stamp(-50.0, False) is None
    # zero liquidity (the `liq_usd or 0` missing sentinel) -> SKIP, never FAVOR
    assert _deep_combo_stamp(-50.0, 0.0) == "SKIP"
