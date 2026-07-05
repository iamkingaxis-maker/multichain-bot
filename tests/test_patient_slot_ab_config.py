"""badday_flush_patient_slot_ab loads with the slot-cap/hold/winner-uncap A/B
params (green-day winner decode 2026-06-29).

This A/B isolates ONE lever bundle vs the firing baseline badday_flush_nf15:
relax the 3-slot cap, extend hold, and uncap the winner — while KEEPING the
tight loss cuts (the decode's edge) and an IDENTICAL entry gate.
"""
from core.bot_config import BotConfig


def test_patient_slot_ab_loads_with_ab_params():
    c = BotConfig.from_json("config/bots/badday_flush_patient_slot_ab.json")
    assert c.bot_id == "badday_flush_patient_slot_ab"
    # 2026-07: patient_slot RETIRED (commit a603628) — config preserved,
    # disabled. The A/B param assertions below still pin the preserved levers.
    assert c.enabled is False
    assert c.live_probe is False                       # PAPER A/B only

    # --- the levers under test ---
    assert c.max_concurrent_positions == 12            # relaxed from the 3-slot churn cap
    assert c.time_stop_minutes is None                 # no hard time-box (winners get room)
    assert c.slow_bleed_minutes == 240                 # extended hold (was 60)
    # uncap the winner: take a SMALL partial at TP1, ride the rest on a wide trail
    assert c.tp1_sell_fraction == 0.25                 # not the baseline 0.75 cap-out
    assert c.trail_pp == 12.0                           # wide trail lets the runner run
    assert c.tp2_pct >= 30.0 and c.tp2_pct > c.tp1_pct  # let it run before the 2nd trim
    assert (c.tp1_sell_fraction + c.tp2_sell_fraction) <= 1.0  # remainder rides

    # --- tight loss cuts KEPT (the decode says these are our edge) ---
    assert c.bot_id.startswith("badday_")              # keeps the -7 IN_FLIGHT_FLOOR
    assert c.hard_stop_pct == -12.0
    assert c.fast_bail_pnl_pct == -9.0
    assert c.pre_stop_bail_pnl_pct == -3.0
    assert c.ng_faststop_exit_enabled is True          # never-green fast cut
    assert c.never_runner_exit_enabled is True         # winner-safe loser cut (peak<3)

    # --- NOT the patient_sleeve dud: baseline entry that actually fires ---
    assert c.winner_select_entry is False              # NOT the FAIL-CLOSED starver
    assert c.microcap_mandate is False


def test_patient_slot_ab_entry_identical_to_baseline_nf15():
    """The A/B must share the exact firing entry of badday_flush_nf15_live so
    the slot/hold/exit lever is isolated (entry held constant)."""
    ab = BotConfig.from_json("config/bots/badday_flush_patient_slot_ab.json")
    base = BotConfig.from_json("config/bots/badday_flush_nf15_live.json")
    assert ab.entry_gate == base.entry_gate
    assert ab.filters_enforced == base.filters_enforced
    assert ab.mcap_min == base.mcap_min
    assert ab.mcap_max == base.mcap_max
    assert ab.age_h_min == base.age_h_min
    assert ab.vol_h1_min == base.vol_h1_min
    assert ab.min_triggers_to_fire == base.min_triggers_to_fire
    assert ab.entry_stack_exempt == base.entry_stack_exempt
    # but the slot cap is the lever that differs
    assert ab.max_concurrent_positions != base.max_concurrent_positions
