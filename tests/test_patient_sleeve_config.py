"""patient_sleeve paper config loads with the patient A/B params (2026-06-26)."""
from core.bot_config import BotConfig


def test_patient_sleeve_loads_with_patient_params():
    c = BotConfig.from_json("config/bots/patient_sleeve.json")
    assert c.bot_id == "patient_sleeve"
    assert not c.bot_id.startswith("badday_")          # skips the -7 IN_FLIGHT_FLOOR
    assert c.enabled is True
    assert c.winner_select_entry is True               # the entry filter
    assert c.hard_stop_pct == -22.0                    # winner-floor, not -6/-15
    assert c.time_stop_minutes == 240                  # patient, not ~5.6min
    assert c.tp1_pct == 15.0 and c.tp1_sell_fraction == 0.25   # partial-then-ride
    assert c.tp2_pct > c.tp1_pct                        # TP ordering sane
    assert c.max_concurrent_positions >= 20            # the Little's-Law slot budget
    assert c.microcap_mandate is True                  # in the lane for sub-floor too
    assert c.antirug_floor_exempt is False             # rug guards STAY on
    assert (c.tp1_sell_fraction + c.tp2_sell_fraction) < 1.0    # remainder rides the trail
