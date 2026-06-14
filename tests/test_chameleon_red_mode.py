"""Chameleon RED-NIGHT MODE (2026-06-14, AxiS): on a red tape the regime OVERRIDES
the board — the chameleon drops its momentum-prone board archetype and adopts the
deep-flush capitulation profile (the measured red-survivor: deepflush_timebox went
+$45 @ $4.50/tr while momentum/timebox_probe bled -$138 on 06-14's red night)."""
import types
from core import meta_chameleon as mc


def _green_config():
    return types.SimpleNamespace(
        bot_id="meta_chameleon",
        entry_gate=(("wash_suspected", "<=", 0), ("liquidity_usd", ">=", 25000),
                    ("entry_age_hours", "<=", 24)),
        triggers_allowed=["deep_1h_dip", "pullback_in_uptrend", "chart_quality_bottom"],
        time_stop_minutes=240.0, tp1_pct=20.0, hard_stop_pct=-60.0,
        tp1_sell_fraction=0.6, tp2_pct=999.0, tp2_sell_fraction=0.0, trail_pp=8.0,
    )


def test_regime_is_red_only_on_bad_verdict():
    red = types.SimpleNamespace(_cycle_regime={"sol_pc_h24": -1.0, "regime_h1_neg_pct": 50})   # broad red
    good = types.SimpleNamespace(_cycle_regime={"sol_pc_h24": -1.5, "regime_h1_neg_pct": 18})  # good dip-buy tape
    neutral = types.SimpleNamespace(_cycle_regime={"sol_pc_h24": 0.5, "regime_h1_neg_pct": 30})
    none = types.SimpleNamespace()                                                              # nothing stashed
    assert mc._regime_is_red(red) is True
    assert mc._regime_is_red(good) is False
    assert mc._regime_is_red(neutral) is False
    assert mc._regime_is_red(none) is False     # fail-safe: no regime -> not red (normal mode)


def test_apply_red_profile_swaps_entry_triggers_geometry():
    mc._GREEN_SNAP.pop("meta_chameleon", None)
    c = _green_config()
    mc._apply_red_profile(c)
    gate_feats = [str(cond[0]) for cond in c.entry_gate]
    assert "wash_suspected" in gate_feats and "liquidity_usd" in gate_feats        # safety rails KEPT
    assert "shape_90m_drawdown_from_max_pct" in gate_feats and "1m_volume_spike" in gate_feats  # deep-flush ADDED
    assert set(c.triggers_allowed) == set(mc.RED_TRIGGERS)                          # capitulation-only
    assert "pullback_in_uptrend" not in c.triggers_allowed                         # momentum dropped
    assert c.time_stop_minutes == 6.0 and c.tp1_pct == 6.0 and c.hard_stop_pct == -12.0  # fast box, raw
    assert c.tp1_sell_fraction == 0.8 and c.trail_pp == 2.0


def test_restore_green_is_exact():
    mc._GREEN_SNAP.pop("meta_chameleon", None)
    c = _green_config()
    orig_gate, orig_trig = c.entry_gate, list(c.triggers_allowed)
    mc._apply_red_profile(c)
    mc._restore_green(c)
    assert c.entry_gate == orig_gate                       # entry_gate restored exactly
    assert list(c.triggers_allowed) == orig_trig           # triggers restored
    assert c.tp1_sell_fraction == 0.6 and c.trail_pp == 8.0 and c.tp2_pct == 999.0


def test_apply_red_profile_idempotent_no_duplicate_conditions():
    # applying twice (e.g. red persists across cycles) must NOT stack duplicate
    # deep-flush conditions, and must NOT lose the original green snapshot.
    mc._GREEN_SNAP.pop("meta_chameleon", None)
    c = _green_config()
    mc._apply_red_profile(c)
    snap_after_first = dict(mc._GREEN_SNAP["meta_chameleon"])
    mc._apply_red_profile(c)
    dd = [cond for cond in c.entry_gate if str(cond[0]) == "shape_90m_drawdown_from_max_pct"]
    assert len(dd) == 1                                    # not duplicated
    assert mc._GREEN_SNAP["meta_chameleon"] == snap_after_first   # green snapshot preserved (restore stays exact)
    # and a restore after a double-apply is still exact
    mc._restore_green(c)
    assert any(str(cond[0]) == "entry_age_hours" for cond in c.entry_gate)
    assert "pullback_in_uptrend" in c.triggers_allowed
